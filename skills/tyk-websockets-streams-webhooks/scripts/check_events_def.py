#!/usr/bin/env python3
"""Check the Tyk Streams and event handler settings of a Tyk OAS API definition before loading it.

Each finding is something that loads on Tyk Gateway 5.16.0 and then behaves other than its
author meant (the skill's reference files give the responses):

    ERROR  the definition is refused (an event handler after the first is stored but wrong),
           or every request fails
    WARN   the setting is accepted and does something other than intended
    NOTE   behaviour worth telling the user about

    check_events_def.py api.oas.json

General wiring (listen path, middleware keys, authentication blocks) is checked by the
`tyk-api-definitions-routing` skill's `check_tyk_oas.py`; run both.

Standard library only. Exit status: 0 with no errors, 1 with at least one error, 2 when the
file cannot be read.
"""
from __future__ import annotations

import json
import re
import sys

EXT = "x-tyk-api-gateway"
STREAMING = "x-tyk-streaming"

# The connectors the Tyk Streams configuration reference documents and the Dashboard validates:
# https://tyk.io/docs/api-management/stream-config (Connector Validation).
DOCUMENTED_CONNECTORS = {"amqp_0_9", "amqp_1", "broker", "http_client", "http_server", "kafka", "mqtt"}
NOT_CONNECTORS = {"label", "processors"}

# Trigger names a Tyk OAS event handler accepts on Tyk Gateway 5.16.0: the `trigger` enum of the
# Gateway's public x-tyk-api-gateway schema at v5.16.0 (apidef/oas/schema/x-tyk-api-gateway.json).
TRIGGERS = {
    "RatelimitExceeded", "OrgRateLimitExceeded", "RateLimitSmoothingUp", "RateLimitSmoothingDown",
    "QuotaExceeded", "OrgQuotaExceeded", "TriggerExceeded", "AuthFailure", "KeyExpired",
    "UpstreamOAuthError", "VersionFailure", "BreakerTriggered", "BreakerTripped", "BreakerReset",
    "HostDown", "HostUp", "TokenCreated", "TokenUpdated", "TokenDeleted", "CertificateExpiringSoon",
    "CertificateExpired",
}
# Events the Gateway fires (docs, Event Types) that are not accepted as a Tyk OAS trigger on 5.16.0.
FIRED_NOT_TRIGGERS = {"OAuth2ScopeCheckFailed", "OAuth2ExchangeSucceeded", "OAuth2ExchangeFailed"}
ZERO_DURATION = re.compile(r"^\s*(0+(\.0+)?(h|m|s|ms)?)*\s*$")
# The cooldownPeriod pattern of the same schema: whole hours, minutes and seconds only.
COOLDOWN = re.compile(r"^(\d+h)?(\d+m)?(\d+s)?$")


def enabled(block) -> bool:
    return isinstance(block, dict) and bool(block.get("enabled"))


def connectors(side: dict) -> list[str]:
    """Connector names used in an input or output block, including inside a broker."""
    names = []
    if not isinstance(side, dict):
        return names
    for name, conf in side.items():
        if name in NOT_CONNECTORS:
            continue
        names.append(name)
        if name == "broker" and isinstance(conf, dict):
            for child in (conf.get("inputs") or []) + (conf.get("outputs") or []):
                names.extend(connectors(child))
    return names


def check_streams(document: dict, add) -> None:
    streams = ((document.get(STREAMING) or {}).get("streams") or {})
    if not streams:
        return
    ext = document.get(EXT) or {}
    server = ext.get("server") or {}
    auth = server.get("authentication") or {}
    glob = ((ext.get("middleware") or {}).get("global") or {})
    raw = json.dumps(document.get(STREAMING))

    if "$tyk_context." in raw and not enabled(glob.get("contextVariables")):
        add("WARN", f"{STREAMING}", "uses `$tyk_context.` but `middleware.global.contextVariables` is not "
            "enabled: every value resolves to an empty string (a per-client consumer group becomes one "
            "shared group)")
    if enabled(glob.get("transformRequestHeaders")):
        add("NOTE", f"{EXT}.middleware.global.transformRequestHeaders", "header transforms do not reach "
            "the stream; to stamp the caller into messages use `$tyk_meta.<field>` or `$tyk_context.<name>` "
            "inside the stream configuration")

    for sname, stream in streams.items():
        if not isinstance(stream, dict):
            continue
        base = f"{STREAMING}.streams.{sname}"
        inp, out = stream.get("input") or {}, stream.get("output") or {}
        for side_name, side in (("input", inp), ("output", out)):
            for name in connectors(side):
                if name not in DOCUMENTED_CONNECTORS:
                    add("WARN", f"{base}.{side_name}.{name}", "not a documented Tyk Streams connector, so it is "
                        "not supported; the documented list is amqp_0_9, amqp_1, broker, http_client, "
                        "http_server, kafka, mqtt. A name the Gateway does not know answers 500 "
                        '{"error": "invalid route handler"}')

        if "http_server" in inp and enabled(auth) and not auth.get("stripAuthorizationData"):
            add("WARN", f"{EXT}.server.authentication", "the client's credential reaches the stream as "
                "message metadata; set `stripAuthorizationData: true` so it cannot be written to the broker")

        hso = out.get("http_server") if isinstance(out.get("http_server"), dict) else None
        if hso is not None:
            stream_path = hso.get("stream_path", "/get/stream")
            if stream_path and hso.get("stream_format", "raw_bytes") != "event_source":
                add("NOTE", f"{base}.output.http_server", f"`{stream_path}` sends newline-separated messages as "
                    "text/plain, not Server-Sent Events; set `stream_format: event_source` for browser "
                    "EventSource clients")
        kin = inp.get("kafka") if isinstance(inp.get("kafka"), dict) else None
        if kin is not None:
            group = str(kin.get("consumer_group", ""))
            if hso is not None and "$tyk_" not in group:
                add("NOTE", f"{base}.input.kafka.consumer_group", "one consumer group for every client: "
                    "connected clients share the messages round-robin, each message reaches one client. "
                    "For every client to get every message, make the group per client "
                    "(for example `$tyk_meta.<field>` or a JWT claim)")
            if kin.get("start_from_oldest", True) is not False:
                add("NOTE", f"{base}.input.kafka", "`start_from_oldest` defaults to true: a new consumer "
                    "group starts with the oldest message still in the topic")


def check_event_handlers(document: dict, add) -> None:
    server = ((document.get(EXT) or {}).get("server") or {})
    handlers = server.get("eventHandlers") or []
    for i, h in enumerate(handlers):
        if not isinstance(h, dict):
            continue
        base = f"{EXT}.server.eventHandlers[{i}]"
        # The Gateway API checks the fields of the first handler only; later ones are stored as written.
        if i == 0:
            refused = "the Gateway API refuses the definition"
        else:
            refused = ("the Gateway API checks only the first handler's fields, so this one is stored "
                       "as written; fix it before loading")
        trigger, kind = h.get("trigger", ""), h.get("type", "")
        if trigger in FIRED_NOT_TRIGGERS:
            add("ERROR", f"{base}.trigger", f"`{trigger}` is fired by the Gateway but is not accepted as a "
                f"Tyk OAS trigger on 5.16.0: {refused}")
        elif trigger not in TRIGGERS:
            add("ERROR", f"{base}.trigger", f"`{trigger}` is not a Tyk OAS trigger name on 5.16.0 (names are "
                f"exact and case-sensitive, for example RatelimitExceeded): {refused}; a stored handler "
                "with this name never matches an event")
        if kind not in ("webhook", "log", "custom"):
            add("ERROR", f"{base}.type", f"must be `webhook`, `log` or `custom`: {refused}")
        if kind == "log":
            if not h.get("name"):
                add("ERROR", f"{base}.name", f"a `log` handler without `name` (`name is required`): {refused}")
            if not h.get("logPrefix"):
                add("ERROR", f"{base}.logPrefix", f"a `log` handler without `logPrefix` (`logPrefix is "
                    f"required`): {refused}")
        if kind != "webhook":
            continue
        global_hook = bool(h.get("id"))
        template = h.get("bodyTemplate")
        if not global_hook and not template:
            add("ERROR", f"{base}.bodyTemplate", f"required for a local webhook (`bodyTemplate is required`): "
                f"{refused}")
        if not global_hook and not str(h.get("url", "")).startswith(("http://", "https://")):
            add("WARN", f"{base}.url", "not an absolute http:// or https:// URL: the definition loads, but "
                "every delivery fails (`unsupported protocol scheme` in the Gateway log)")
        if template and str(template).startswith("templates/"):
            add("WARN", f"{base}.bodyTemplate", "resolved under the Gateway's `template_path`, so "
                f"`{template}` is looked for at <template_path>/{template}; the load fails and the default "
                "template is used. Give the path relative to `template_path` (for example "
                "`custom/event.json`) or as an absolute path inside it")
        if template and str(template).rsplit("/", 1)[-1] == "default_webhook.json":
            add("NOTE", f"{base}.bodyTemplate", "the default template includes the `key` field "
                "(docs, Webhook payload); use your own template without `{{.Meta.Key}}` unless the "
                "receiver needs it")
        cooldown = h.get("cooldownPeriod")
        if cooldown is None:
            add("ERROR", f"{base}.cooldownPeriod", f"missing (`cooldownPeriod is required`, also for a "
                f"global webhook with `id`): {refused}. Use at least `1s`")
        elif not COOLDOWN.match(str(cooldown)):
            add("ERROR", f"{base}.cooldownPeriod", f"`{cooldown}` does not match `^(\\d+h)?(\\d+m)?(\\d+s)?$` "
                f"(whole hours, minutes and seconds, no `ms` or decimals): {refused}")
        elif ZERO_DURATION.match(str(cooldown)):
            add("WARN", f"{base}.cooldownPeriod", "zero: a payload identical to one already sent is never "
                "sent again (the de-duplication record does not expire). Use at least `1s`")


def check(document: dict) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    def add(level, where, text):
        out.append((level, where, text))

    if EXT not in document:
        add("ERROR", "document", f"no `{EXT}` block: this is not a Tyk OAS API definition")
        return out
    check_streams(document, add)
    check_event_handlers(document, add)
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: check_events_def.py <tyk-oas-definition.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as fh:
            document = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    findings = check(document)
    for level, where, text in findings:
        print(f"{level:5} {where}: {text}")
    counts = {lvl: sum(1 for f in findings if f[0] == lvl) for lvl in ("ERROR", "WARN", "NOTE")}
    print(f"{counts['ERROR']} error(s), {counts['WARN']} warning(s), {counts['NOTE']} note(s)")
    return 1 if counts["ERROR"] else 0


if __name__ == "__main__":
    sys.exit(main())
