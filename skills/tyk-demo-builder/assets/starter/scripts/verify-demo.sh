#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

mkdir -p "${ROOT_DIR}/evidence/bodies" "${ROOT_DIR}/notes"
results='[]'
failed=0
identity_provider_mode="$(jq -r '.identity_provider_mode' "${ROOT_DIR}/demo.config.json")"

record() {
  local test_id="$1" status="$2" detail="$3"
  results="$(jq -c --arg id "${test_id}" --arg status "${status}" --arg detail "${detail}" '. + [{test_id:$id,status:$status,detail:$detail}]' <<<"${results}")"
  [[ "${status}" != failed ]] || failed=1
  printf '%-18s %-8s %s\n' "${test_id}" "${status}" "${detail}"
}

# evidence/results.json, notes/validation.md and the requirement rows are written on EVERY
# exit, so a check that aborts the run (set -e on an unexpected failure) still leaves the
# results gathered so far, plus a failed VERIFY-RUN-001 row naming the line it stopped at.
# The command is recorded as written in this script, so no expanded secret reaches the note.
verify_completed=false
finish_verification() {
  local status="$1" command="$2" line="$3"
  trap - EXIT
  set +e
  if [[ "${verify_completed}" != true ]]; then
    record VERIFY-RUN-001 failed "verify-demo.sh stopped at line ${line} (exit ${status}) on: ${command:0:160}; the checks after it did not run"
    [[ "${status}" != 0 ]] || status=1
  else
    status="${failed}"
  fi
  # Every result carries the UTC time this run wrote it, so update-requirements.py can say
  # which run a requirement status belongs to.
  printf '%s\n' "${results}" | jq --arg run "$(date -u +%Y-%m-%dT%H:%M:%SZ)" 'map(. + {run: $run})' \
    >"${ROOT_DIR}/evidence/results.json" || status=1
  # notes/validation.md is for curated commands, responses and citations; only the block
  # between its generated-results markers is rewritten here.
  python3 "${ROOT_DIR}/scripts/render-validation.py" || status=1
  python3 "${ROOT_DIR}/scripts/update-requirements.py" || status=1
  exit "${status}"
}
trap 'finish_verification "$?" "${BASH_COMMAND}" "${LINENO}"' EXIT
# -- end of the results trap --

expect_code() {
  local expected="$1" output="$2"
  [[ "${output}" == "${expected}" ]]
}

bodies="${ROOT_DIR}/evidence/bodies"

# code_of <body-file> <curl-or-gw_curl> <args...>: the HTTP status only (000 when the call
# never got an answer); the response body is kept in the file as evidence.
code_of() {
  local body="$1" code
  shift
  code="$("$@" -sS -o "${body}" -w '%{http_code}' 2>/dev/null)" || true
  printf '%s' "${code:-000}"
}

# retry_code <expected> <attempts> <body-file> <curl-or-gw_curl> <args...>
# Repeats a request one second apart until it returns the expected status and prints the
# last status seen. Gateways load an API change asynchronously after an import or a
# restart, so a positive path gets a short grace period; negative paths are asserted once.
retry_code() {
  local want="$1" attempts="$2" body="$3" code=000
  shift 3
  for _ in $(seq 1 "${attempts}"); do
    code="$(code_of "${body}" "$@")"
    [[ "${code}" == "${want}" ]] && break
    sleep 1
  done
  printf '%s' "${code}"
}

dashboard_get() {
  curl -fsS "${DASHBOARD_URL}$1" -H "authorization: ${DASHBOARD_API_KEY}"
}

# HEALTH-001, for every bundle: the endpoints a load balancer or an orchestrator probe would
# use, and the gateway admin API refusing a caller without the node secret.
health_ok=true
health_detail=""
gateway_labels=(gateway)
gateway_targets=("${GATEWAY_URL}")
if module_enabled gateway-sharding; then
  gateway_labels=("gateway" "external gateway")
  gateway_targets+=("${GATEWAY_EXTERNAL_URL}")
fi
for index in "${!gateway_targets[@]}"; do
  body="${bodies}/health-gateway-${index}-hello.json"
  components=false
  code=000
  for _ in $(seq 1 20); do
    code="$(code_of "${body}" gw_curl "${gateway_targets[index]}/hello")"
    if [[ "${code}" == 200 ]] && jq -e '.status == "pass" and .details.redis.status == "pass" and .details.dashboard.status == "pass"' "${body}" >/dev/null 2>&1; then
      components=true
      break
    fi
    sleep 1
  done
  [[ "${components}" == true ]] || health_ok=false
  health_detail+="${gateway_labels[index]} /hello ${code} (redis and dashboard pass: ${components}); "
done
dashboard_hello="$(retry_code 200 20 "${bodies}/health-dashboard-hello.json" curl "${DASHBOARD_URL}/hello")"
[[ "${dashboard_hello}" == 200 ]] || health_ok=false
health_detail+="dashboard /hello ${dashboard_hello}; "
if module_enabled portal; then
  portal_live="$(retry_code 200 20 "${bodies}/health-portal-live.txt" curl "${PORTAL_URL}/live")"
  portal_ready="$(retry_code 200 20 "${bodies}/health-portal-ready.txt" curl "${PORTAL_URL}/ready")"
  [[ "${portal_live}" == 200 && "${portal_ready}" == 200 ]] || health_ok=false
  health_detail+="portal /live ${portal_live}, /ready ${portal_ready}; "
fi
# Pump publishes no host port, so its health endpoint is read from inside the network.
pump_health="$(compose exec -T demo-upstream python -c 'import sys, urllib.request
response = urllib.request.urlopen("http://tyk-pump:8083/health", timeout=5)
sys.stdout.write(str(response.status))' 2>/dev/null || true)"
[[ "${pump_health}" == 200 ]] || health_ok=false
health_detail+="pump /health ${pump_health:-000}; "
# Every gateway's admin API, not only the primary one: each node reads its own secret.
admin_detail=""
for index in "${!gateway_targets[@]}"; do
  admin_missing="$(code_of "${bodies}/health-admin-${index}-no-secret.json" gw_curl "${gateway_targets[index]}/tyk/apis")"
  admin_wrong="$(code_of "${bodies}/health-admin-${index}-wrong-secret.json" gw_curl "${gateway_targets[index]}/tyk/apis" -H 'x-tyk-authorization: not-the-node-secret')"
  admin_ok="$(code_of /dev/null gw_curl "${gateway_targets[index]}/tyk/apis" -H "x-tyk-authorization: ${NODE_SECRET}")"
  [[ "${admin_missing}" == 403 && "${admin_wrong}" == 403 && "${admin_ok}" == 200 ]] || health_ok=false
  admin_detail+="${admin_detail:+; }${gateway_labels[index]} /tyk/apis without secret ${admin_missing}, wrong secret ${admin_wrong}, node secret ${admin_ok}"
done
health_detail+="${admin_detail}"
if ${health_ok}; then
  record HEALTH-001 passed "${health_detail}"
else
  record HEALTH-001 failed "${health_detail}"
fi

core="${GATEWAY_URL}/demo/__SLUG__/core"
if module_enabled rest; then
  if gw_curl -fsS "${core}/v1/echo" -H 'X-Correlation-Id: verify-rest' | jq -e '.headers["x-correlation-id"] == "verify-rest"' >/dev/null; then
    record REST-001 passed "Gateway proxied to reflected upstream"
  else
    record REST-001 failed "Core REST call failed"
  fi
fi

if module_enabled api-key; then
  unauth="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/demo/__SLUG__/api-key/v1/echo")"
  if [[ -n "${DEMO_API_KEY:-}" ]] && [[ "${unauth}" != 200 ]] && gw_curl -fsS "${GATEWAY_URL}/demo/__SLUG__/api-key/v1/echo" -H "Authorization: ${DEMO_API_KEY}" >/dev/null; then
    record AUTH-KEY-001 passed "Unauthorized request rejected and owned key accepted"
  else
    record AUTH-KEY-001 failed "API key positive or negative path failed"
  fi
fi

if module_enabled basic-auth; then
  unauth="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/demo/__SLUG__/basic-auth/v1/echo")"
  if [[ "${unauth}" != 200 ]] && gw_curl -fsS -u "${BASIC_AUTH_USERNAME}:${BASIC_AUTH_PASSWORD}" "${GATEWAY_URL}/demo/__SLUG__/basic-auth/v1/echo" >/dev/null; then
    record AUTH-BASIC-001 passed "Basic Auth rejected missing credentials and accepted the owned user"
  else
    record AUTH-BASIC-001 failed "Basic Auth positive or negative path failed"
  fi
fi

if module_enabled jwt; then
  if [[ "${identity_provider_mode}" == external ]]; then
    record AUTH-JWT-001 blocked "External issuer, JWKS, audience, token and scope-policy proof are required"
  else
  token="$(curl -fsS "http://localhost:${IDENTITY_PORT}/token" -H 'Content-Type: application/json' --data '{"sub":"verification-client","scope":"demo.read"}' | jq -r '.access_token')"
  wrong_audience="$(curl -fsS "http://localhost:${IDENTITY_PORT}/token" -H 'Content-Type: application/json' --data '{"aud":"wrong-audience","scope":"demo.read"}' | jq -r '.access_token')"
  insufficient_scope="$(curl -fsS "http://localhost:${IDENTITY_PORT}/token" -H 'Content-Type: application/json' --data '{"scope":"demo.denied"}' | jq -r '.access_token')"
  invalid="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/demo/__SLUG__/jwt/v1/echo" -H 'Authorization: Bearer invalid')"
  audience_code="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/demo/__SLUG__/jwt/v1/echo" -H "Authorization: Bearer ${wrong_audience}")"
  scope_code="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/demo/__SLUG__/jwt/v1/echo" -H "Authorization: Bearer ${insufficient_scope}")"
  if [[ "${invalid}" != 200 && "${audience_code}" != 200 && "${scope_code}" != 200 ]] && gw_curl -fsS "${GATEWAY_URL}/demo/__SLUG__/jwt/v1/echo" -H "Authorization: Bearer ${token}" >/dev/null; then
    record AUTH-JWT-001 passed "RS256, issuer/audience and scope-to-policy mapping were enforced"
  else
    record AUTH-JWT-001 failed "JWT positive or negative path failed"
  fi
  fi
fi

if module_enabled oauth; then
  token_response="$(gw_curl -fsS -u "${OAUTH_CLIENT_ID}:${OAUTH_CLIENT_SECRET}" "${GATEWAY_URL}/demo/__SLUG__/oauth/oauth/token" -H 'Content-Type: application/x-www-form-urlencoded' --data 'grant_type=client_credentials')"
  token="$(jq -r '.access_token // empty' <<<"${token_response}")"
  invalid="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/demo/__SLUG__/oauth/v1/echo" -H 'Authorization: Bearer invalid')"
  if [[ -n "${token}" && "${invalid}" != 200 ]] && gw_curl -fsS "${GATEWAY_URL}/demo/__SLUG__/oauth/v1/echo" -H "Authorization: Bearer ${token}" >/dev/null; then
    record AUTH-OAUTH-001 passed "OAuth client credentials token issued and enforced"
  else
    record AUTH-OAUTH-001 failed "OAuth positive or negative path failed"
  fi
fi

if module_enabled chained-auth; then
  endpoint="${GATEWAY_URL}/demo/__SLUG__/chained-auth/v1/echo"
  unauth="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${endpoint}")"
  key_code="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${endpoint}" -H "X-API-Key: ${DEMO_API_KEY}")"
  basic_code="$(gw_curl -sS -o /dev/null -w '%{http_code}' -u "${CHAINED_AUTH_USERNAME}:${CHAINED_AUTH_PASSWORD}" "${endpoint}")"
  if [[ "${unauth}" != 200 && "${key_code}" == 200 && "${basic_code}" == 200 ]]; then
    record AUTH-CHAIN-001 passed "Compliant security mode accepted API key OR Basic Auth"
  else
    record AUTH-CHAIN-001 failed "Multi-auth codes unauth=${unauth} api-key=${key_code} basic=${basic_code}"
  fi
fi

if module_enabled routing; then
  body='{"InboundData":{"country_code":"UK"},"OutboundData":{"country_code":"US"}}'
  if gw_curl -fsS "${core}/v1/route" -H 'Content-Type: application/json' --data "${body}" | jq -e '.region == "uk"' >/dev/null; then
    record ROUTE-001 passed "InboundData country won conflicting route"
  else
    record ROUTE-001 failed "Nested route precedence failed"
  fi
fi

if module_enabled transforms; then
  simple_ok=false
  nested_ok=false
  gw_curl -fsS "${core}/v1/transform" -H 'Content-Type: application/json' --data '{"weight_lb":3,"country":"United Kingdom"}' | jq -e '.body.weight_kg == 1.36 and .body.country_code == "GB"' >/dev/null && simple_ok=true
  gw_curl -fsS "${core}/v1/transform-nested" -H 'Content-Type: application/json' --data '{"parcel":{"weight_lb":3,"items":[{"weight_lb":1},{"weight_lb":2}]},"InboundData":{"country":"United Kingdom"}}' | jq -e '.body.parcel.weight_in_kilograms == 1.36 and .body.parcel.items[0].weight_in_kilograms == 0.45 and .body.parcel.items[1].weight_in_kilograms == 0.91 and .body.inbound_country_code == "GB"' >/dev/null && nested_ok=true
  if ${simple_ok} && ${nested_ok}; then
    record TRANSFORM-001 passed "Scalar lookup and repeated parcel/item fields transformed with Go templates"
  else
    record TRANSFORM-001 failed "Request transformation failed"
  fi
fi

if module_enabled validation; then
  code="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${core}/v1/validated" -H 'Content-Type: application/json' --data '{}')"
  if expect_code 422 "${code}"; then record VALIDATE-001 passed "Invalid payload rejected with 422"; else record VALIDATE-001 failed "Expected 422, got ${code}"; fi
fi

if module_enabled error-overrides; then
  error_ok=true
  for item in '401:AUTHENTICATION_ERROR' '422:VALIDATION_ERROR' '429:RATE_LIMITED' '503:UPSTREAM_UNAVAILABLE'; do
    status="${item%%:*}"
    machine_code="${item#*:}"
    body_file="${ROOT_DIR}/evidence/bodies/error-${status}.json"
    actual="$(gw_curl -sS -o "${body_file}" -w '%{http_code}' "${core}/v1/errors/${status}")"
    [[ "${actual}" == "${status}" ]] || error_ok=false
    jq -e --arg code "${machine_code}" '.error.code == $code and (.error.retryable | type == "boolean") and (has("unsafe_internal_detail") | not)' "${body_file}" >/dev/null || error_ok=false
  done
  if ${error_ok}; then record ERROR-001 passed "401, 422, 429 and 503 responses used safe standardized envelopes"; else record ERROR-001 failed "One or more standardized error assertions failed"; fi
fi

if module_enabled rate-limit; then
  codes=""
  for _ in 1 2 3 4 5; do codes+="$(gw_curl -sS -o /dev/null -w '%{http_code} ' "${core}/v1/limited")"; done
  if [[ "${codes}" == *429* ]]; then record LIMIT-001 passed "Burst produced 429"; else record LIMIT-001 failed "Burst did not produce 429: ${codes}"; fi
fi

if module_enabled quota; then
  payload="$(jq -nc --arg org "${ORG_ID}" --arg api "__SLUG__-api-key" '{org_id:$org,rate:1000,per:60,quota_max:2,quota_renewal_rate:60,access_rights:{($api):{api_id:$api,api_name:"Quota proof",versions:["Default"]}},meta_data:{demo_owner:"__SLUG__",purpose:"quota-proof"}}')"
  response="$(curl -fsS "${DASHBOARD_URL}/api/keys" -H "authorization: ${DASHBOARD_API_KEY}" -H 'Content-Type: application/json' --data "${payload}")"
  quota_key="$(jq -r '.key_id // .key // .Key // .api_model.key // empty' <<<"${response}")"
  codes=""
  for _ in 1 2 3 4; do codes+="$(gw_curl -sS -o /dev/null -w '%{http_code} ' "${GATEWAY_URL}/demo/__SLUG__/api-key/v1/echo" -H "Authorization: ${quota_key}")"; done
  curl -fsS -X DELETE "${DASHBOARD_URL}/api/keys/${quota_key}" -H "authorization: ${DASHBOARD_API_KEY}" >/dev/null || true
  if [[ -n "${quota_key}" && "${codes}" == *403* ]]; then record QUOTA-001 passed "Ephemeral key exceeded quota: ${codes}"; else record QUOTA-001 failed "Quota did not reject requests: ${codes}"; fi
fi

if module_enabled cache; then
  first="$(gw_curl -fsS "${core}/v1/cached")"
  second="$(gw_curl -fsS "${core}/v1/cached")"
  if [[ "$(jq -r '.hits' <<<"${first}")" == "$(jq -r '.hits' <<<"${second}")" ]]; then record CACHE-001 passed "Cached response preserved upstream hit count"; else record CACHE-001 failed "Upstream hit count changed"; fi
fi

if module_enabled circuit-breaker; then
  before="$(curl -fsS "http://localhost:${UPSTREAM_PORT}/stats" | jq -r '.hits')"
  for _ in 1 2 3; do gw_curl -sS -o /dev/null "${core}/v1/breaker"; done
  after_sample="$(curl -fsS "http://localhost:${UPSTREAM_PORT}/stats" | jq -r '.hits')"
  gw_curl -sS -o /dev/null "${core}/v1/breaker"
  after_trip="$(curl -fsS "http://localhost:${UPSTREAM_PORT}/stats" | jq -r '.hits')"
  if (( after_sample > before )) && [[ "${after_trip}" == "${after_sample}" ]]; then record CB-001 passed "Circuit opened and skipped the fourth upstream call"; else record CB-001 failed "Upstream hits before=${before} sampled=${after_sample} after=${after_trip}"; fi
fi

if module_enabled mtls; then
  without="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/demo/__SLUG__/mtls/v1/echo")"
  with="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/demo/__SLUG__/mtls/v1/echo" --cert "${ROOT_DIR}/generated/certs/client.crt" --key "${ROOT_DIR}/generated/certs/client.key")"
  untrusted="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/demo/__SLUG__/mtls/v1/echo" --cert "${ROOT_DIR}/generated/certs/untrusted-client.crt" --key "${ROOT_DIR}/generated/certs/untrusted-client.key")"
  if [[ "${without}" != 200 && "${untrusted}" != 200 && "${with}" == 200 ]]; then record AUTH-MTLS-001 passed "Valid certificate accepted; missing and untrusted certificates rejected"; else record AUTH-MTLS-001 failed "mTLS codes missing=${without} valid=${with} untrusted=${untrusted}"; fi
fi

if module_enabled websocket; then
  if python3 "${ROOT_DIR}/scripts/websocket-check.py" --gateway-url "${GATEWAY_URL}" --path "/demo/__SLUG__/ws/" --api-key "${DEMO_API_KEY}" >/dev/null; then record WS-001 passed "Authenticated WebSocket message echoed through Tyk"; else record WS-001 failed "WebSocket handshake or echo failed"; fi
fi

if module_enabled graphql; then
  if gw_curl -fsS "${GATEWAY_URL}/demo/__SLUG__/graphql/" -H 'Content-Type: application/json' --data '{"query":"{ hello }"}' | jq -e '.data.hello == "hello through Tyk"' >/dev/null; then record GQL-001 passed "GraphQL query proxied"; else record GQL-001 failed "GraphQL query failed"; fi
fi

if module_enabled dcr; then
  if [[ "${identity_provider_mode}" == external ]]; then
    record DCR-001 blocked "External provider registration create/read/update/delete proof is required"
  else
  discovery="$(curl -fsS "http://localhost:${IDENTITY_PORT}/.well-known/openid-configuration")"
  registration="$(curl -fsS "http://localhost:${IDENTITY_PORT}/register" -H 'Content-Type: application/json' --data '{"client_name":"verification-client","grant_types":["client_credentials"]}')"
  client_id="$(jq -r '.client_id' <<<"${registration}")"
  updated='{}'
  [[ -z "${client_id}" ]] || updated="$(curl -fsS -X PUT "http://localhost:${IDENTITY_PORT}/register/${client_id}" -H 'Content-Type: application/json' --data '{"client_name":"updated-verification-client","grant_types":["client_credentials"]}')"
  if jq -e '.registration_endpoint | endswith("/register")' <<<"${discovery}" >/dev/null && [[ -n "${client_id}" ]] && jq -e '.client_name == "updated-verification-client"' <<<"${updated}" >/dev/null && curl -fsS "http://localhost:${IDENTITY_PORT}/register/${client_id}" | jq -e --arg id "${client_id}" '.client_id == $id' >/dev/null; then
    curl -fsS -X DELETE "http://localhost:${IDENTITY_PORT}/register/${client_id}" >/dev/null
    record DCR-001 passed "DCR create/read/update/delete passed"
  else
    record DCR-001 failed "DCR lifecycle failed"
  fi
  fi
fi

if module_enabled streams; then
  compose exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --create --if-not-exists --topic __SLUG__-events >/dev/null
  event_id="stream-proof-$(date +%s)"
  # The first request after an API load or reload can find the stream still starting, so the
  # publish is retried a bounded number of times before the check fails, and the attempt that
  # succeeded is recorded.
  publish_code=000
  publish_attempts=0
  for publish_attempts in 1 2 3 4 5; do
    publish_code="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/demo/__SLUG__/streams/events" -H 'Content-Type: application/json' --data "{\"event_id\":\"${event_id}\"}" || echo 000)"
    [[ "${publish_code}" =~ ^2 ]] && break
    sleep 2
  done
  if [[ ! "${publish_code}" =~ ^2 ]]; then
    record STREAM-001 failed "Stream publish answered HTTP ${publish_code} after ${publish_attempts} attempts"
  else
    consumed="$(compose exec -T kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:9092 --topic __SLUG__-events --from-beginning --timeout-ms 10000 2>/dev/null || true)"
    if grep -q "${event_id}" <<<"${consumed}"; then record STREAM-001 passed "HTTP event was published to Kafka (publish attempt ${publish_attempts})"; else record STREAM-001 failed "Kafka event was not observed"; fi
  fi
fi

if module_enabled otel; then
  trace_id="11111111111111111111111111111111"
  gw_curl -fsS "${core}/v1/echo" -H "traceparent: 00-${trace_id}-2222222222222222-01" >/dev/null
  found=false
  for _ in $(seq 1 15); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' "http://localhost:${TEMPO_PORT}/api/traces/${trace_id}")"
    [[ "${code}" == 200 ]] && { found=true; break; }
    sleep 1
  done
  # Span-derived metrics depend on the spanmetrics flush interval plus the Prometheus scrape
  # interval. The collector config pins the flush to 5s so this resolves quickly; the budget
  # below is a safety net for slower hosts. A genuinely absent metric still fails.
  metric_found=false
  metric_budget="${OTEL_METRIC_BUDGET_SECONDS:-45}"
  for _ in $(seq 1 "${metric_budget}"); do
    metric_found="$(curl -fsS --get "http://localhost:${PROMETHEUS_PORT}/api/v1/query" --data-urlencode 'query=calls_total{service_name="tyk-gateway"}' | jq -r '.data.result | length > 0')"
    [[ "${metric_found}" == true ]] && break
    sleep 1
  done
  log_found=false
  for _ in $(seq 1 10); do
    collector_logs="$(compose logs --since=2m otel-collector 2>/dev/null || true)"
    grep -q "${trace_id}" <<<"${collector_logs}" && { log_found=true; break; }
    sleep 1
  done
  if ${found} && [[ "${metric_found}" == true ]] && ${log_found}; then record OTEL-001 passed "One request: its Gateway trace in Tempo, the span-derived calls_total metric in Prometheus, and the upstream structured log (read by the Collector) carrying the same trace id"; else record OTEL-001 failed "Observability proof trace=${found} span_metric=${metric_found} log=${log_found}"; fi
fi

if module_enabled grpc-plugin; then
  endpoint="${GATEWAY_URL}/demo/__SLUG__/grpc-plugin/v1/echo"
  enriched="$(gw_curl -fsS "${endpoint}" -H "Authorization: ${DEMO_API_KEY}")"
  before_denied="$(curl -fsS "http://localhost:${UPSTREAM_PORT}/stats" | jq -r '.hits')"
  denied_file="${ROOT_DIR}/evidence/bodies/grpc-denied.json"
  denied_code="$(gw_curl -sS -o "${denied_file}" -w '%{http_code}' "${endpoint}" -H "Authorization: ${DEMO_API_KEY}" -H 'X-Deny-Request: true')"
  after_denied="$(curl -fsS "http://localhost:${UPSTREAM_PORT}/stats" | jq -r '.hits')"
  unauthenticated_code="$(gw_curl -sS -o /dev/null -w '%{http_code}' "${endpoint}" -H 'X-Deny-Request: true')"
  after_unauthenticated="$(curl -fsS "http://localhost:${UPSTREAM_PORT}/stats" | jq -r '.hits')"
  if jq -e '.headers["x-grpc-enriched"] == "true"' <<<"${enriched}" >/dev/null \
    && [[ "${denied_code}" == 403 && "${before_denied}" == "${after_denied}" && "${after_denied}" == "${after_unauthenticated}" && "${unauthenticated_code}" != 200 ]] \
    && jq -e '.error.code == "PLUGIN_DENIED"' "${denied_file}" >/dev/null; then
    record PLUGIN-GRPC-001 passed "Post hook enriched an allowed request; auth and ReturnOverrides both skipped upstream"
  else
    record PLUGIN-GRPC-001 failed "gRPC enrichment or ReturnOverrides termination failed"
  fi
fi

if module_enabled go-plugin; then
  response="$(gw_curl -fsS "${GATEWAY_URL}/demo/__SLUG__/go-plugin/v1/echo")"
  if jq -e '.headers["x-demo-post-hook"] == "native-go"' <<<"${response}" >/dev/null; then record PLUGIN-GO-001 passed "Version-matched Go Post hook enriched the upstream request"; else record PLUGIN-GO-001 failed "Go plugin enrichment was not reflected upstream"; fi
fi

if module_enabled gateway-sharding; then
  # tyk-gateway is not segmented and loads every API; tyk-gateway-external is segmented on
  # the tag "external" and loads only APIs whose server.gatewayTags carry it.
  if [[ -z "${PLATFORM_API_KEY:-}" ]]; then
    record SHARD-001 blocked "PLATFORM_API_KEY missing; run scripts/configure-demo.sh"
  else
    auth=(-H "Authorization: ${PLATFORM_API_KEY}")
    external_path="/demo/__SLUG__/external-services/v1/echo"
    internal_path="/demo/__SLUG__/internal-services/v1/echo"
    external_on_primary="$(retry_code 200 20 "${bodies}/shard-external-api-primary-gateway.json" gw_curl "${GATEWAY_URL}${external_path}" "${auth[@]}")"
    external_on_external="$(retry_code 200 20 "${bodies}/shard-external-api-external-gateway.json" gw_curl "${GATEWAY_EXTERNAL_URL}${external_path}" "${auth[@]}")"
    internal_on_primary="$(retry_code 200 20 "${bodies}/shard-internal-api-primary-gateway.json" gw_curl "${GATEWAY_URL}${internal_path}" "${auth[@]}")"
    internal_on_external="$(code_of "${bodies}/shard-internal-api-external-gateway.txt" gw_curl "${GATEWAY_EXTERNAL_URL}${internal_path}" "${auth[@]}")"
    # What the segmented gateway actually loaded, from its own admin API.
    loaded_file="${bodies}/shard-external-gateway-apis.json"
    gw_curl -fsS "${GATEWAY_EXTERNAL_URL}/tyk/apis" -H "x-tyk-authorization: ${NODE_SECRET}" 2>/dev/null \
      | jq '[.[] | {api_id, name, tags: (.tags // [])}] | sort_by(.api_id)' >"${loaded_file}" 2>/dev/null || echo '[]' >"${loaded_file}"
    external_only=false
    jq -e '(length > 0) and all(.[]; .tags | index("external") != null)
           and ([.[].api_id] | index("__SLUG__-external-services") != null and index("__SLUG__-internal-services") == null)' \
      "${loaded_file}" >/dev/null 2>&1 && external_only=true
    # The Dashboard's gateway list (the "Nodes & licenses" page) identifies nodes by hostname.
    nodes_ok=false
    for _ in $(seq 1 20); do
      dashboard_get /api/system/nodes >"${bodies}/shard-dashboard-nodes.json" 2>/dev/null || true
      if jq -e '[.data.nodes[]?.hostname] | index("__GATEWAY_HOSTNAME__") != null and index("__GATEWAY_EXTERNAL_HOSTNAME__") != null' \
        "${bodies}/shard-dashboard-nodes.json" >/dev/null 2>&1; then
        nodes_ok=true
        break
      fi
      sleep 1
    done
    detail="external API: primary ${external_on_primary}, external ${external_on_external}; internal API: primary ${internal_on_primary}, external ${internal_on_external}; external gateway loaded only external-tagged APIs: ${external_only}; Dashboard node list shows __GATEWAY_HOSTNAME__ and __GATEWAY_EXTERNAL_HOSTNAME__: ${nodes_ok}"
    if [[ "${external_on_primary}" == 200 && "${external_on_external}" == 200 && "${internal_on_primary}" == 200 && "${internal_on_external}" == 404 ]] \
      && ${external_only} && ${nodes_ok}; then
      record SHARD-001 passed "${detail}"
    else
      record SHARD-001 failed "${detail}"
    fi
  fi
fi

if module_enabled custom-domain; then
  # Keep existing URLs during migration: the legacy hostname and path, stripped before the
  # upstream. The API matches only its custom domain.
  legacy_host="api.legacy.__SLUG__.invalid"
  with_host="$(retry_code 200 20 "${bodies}/domain-with-host.json" gw_curl "${GATEWAY_URL}/api/v1/records/42?fields=name" -H "Host: ${legacy_host}")"
  stripped=false
  jq -e '.path == "/42?fields=name"' "${bodies}/domain-with-host.json" >/dev/null 2>&1 && stripped=true
  without_host="$(code_of "${bodies}/domain-without-host.txt" gw_curl "${GATEWAY_URL}/api/v1/records/42?fields=name")"
  other_host="$(code_of "${bodies}/domain-other-host.txt" gw_curl "${GATEWAY_URL}/api/v1/records/42?fields=name" -H "Host: api.other.__SLUG__.invalid")"
  detail="Host ${legacy_host}: ${with_host} (upstream saw /42?fields=name: ${stripped}); no Host: ${without_host}; other Host: ${other_host}"
  if [[ "${with_host}" == 200 && "${stripped}" == true && "${without_host}" == 404 && "${other_host}" == 404 ]]; then
    record DOMAIN-001 passed "${detail}"
  else
    record DOMAIN-001 failed "${detail}"
  fi
fi

if module_enabled js-plugin; then
  # A pre-authentication JavaScript plugin maps ?user_key= or an app_key header into
  # Authorization and removes the original before the upstream sees the request.
  if [[ -z "${PLATFORM_API_KEY:-}" ]]; then
    record PLUGIN-JS-001 blocked "PLATFORM_API_KEY missing; run scripts/configure-demo.sh"
  else
    endpoint="${GATEWAY_URL}/demo/__SLUG__/compat/v1/echo"
    query_code="$(retry_code 200 20 "${bodies}/plugin-js-user-key-query.json" gw_curl "${endpoint}?user_key=${PLATFORM_API_KEY}&page=2")"
    query_stripped=false
    jq -e '.path == "/v1/echo?page=2"' "${bodies}/plugin-js-user-key-query.json" >/dev/null 2>&1 && query_stripped=true
    header_code="$(code_of "${bodies}/plugin-js-app-key-header.json" gw_curl "${endpoint}" -H "app_key: ${PLATFORM_API_KEY}")"
    none_code="$(code_of "${bodies}/plugin-js-no-credential.json" gw_curl "${endpoint}")"
    wrong_code="$(code_of "${bodies}/plugin-js-wrong-key.json" gw_curl "${endpoint}?user_key=unknown-$(openssl rand -hex 6)")"
    driver="$(jq -r '."x-tyk-api-gateway".middleware.global.pluginConfig.driver // empty' "${ROOT_DIR}/apis/__SLUG__-compat.oas.json")"
    # goja (driver "javascript", Gateway 5.15+) tags the plugin's log() lines with
    # prefix=jsvm-goja, a cheap proof that the new engine ran. otto logs differently, so the
    # line is asserted only for the goja driver.
    engine_ok=true
    engine_detail="driver ${driver}: engine log line not asserted"
    if [[ "${driver}" == javascript ]]; then
      engine_ok=false
      for _ in $(seq 1 10); do
        compose logs --since 10m tyk-gateway 2>/dev/null | grep -F '__SLUG__-credential-mapper' | tail -3 >"${bodies}/plugin-js-gateway-log.txt" || true
        if grep -q 'prefix=jsvm-goja' "${bodies}/plugin-js-gateway-log.txt"; then
          engine_ok=true
          break
        fi
        sleep 1
      done
      engine_detail="driver javascript: goja log line (prefix=jsvm-goja) ${engine_ok}"
    fi
    detail="?user_key=: ${query_code} (upstream saw /v1/echo?page=2, user_key removed: ${query_stripped}); app_key header: ${header_code}; no credential: ${none_code}; unknown key: ${wrong_code}; ${engine_detail}"
    if [[ "${query_code}" == 200 && "${query_stripped}" == true && "${header_code}" == 200 && "${none_code}" == 401 \
      && ( "${wrong_code}" == 401 || "${wrong_code}" == 403 ) && "${engine_ok}" == true ]]; then
      record PLUGIN-JS-001 passed "${detail}"
    else
      record PLUGIN-JS-001 failed "${detail}"
    fi
  fi
fi

if module_enabled key-import; then
  # Keys created by scripts/import-keys.sh with the values the previous gateway issued.
  import_file="${ROOT_DIR}/migration/imported-keys.json"
  endpoint="${GATEWAY_URL}/demo/__SLUG__/api-key/v1/echo"
  accepted_codes=""
  accepted_ok=true
  aliases_ok=true
  while IFS=$'\t' read -r imported_key imported_alias; do
    [[ -n "${imported_key}" ]] || continue
    code="$(retry_code 200 20 "${bodies}/key-import-accepted.json" gw_curl "${endpoint}" -H "Authorization: ${imported_key}")"
    accepted_codes+="${code} "
    [[ "${code}" == 200 ]] || accepted_ok=false
    dashboard_get "/api/keys/${imported_key}" 2>/dev/null | jq -e --arg alias "${imported_alias}" '.data.alias == $alias' >/dev/null 2>&1 || aliases_ok=false
  done < <(jq -r '.keys[] | [.key, .alias] | @tsv' "${import_file}")
  random_code="$(code_of "${bodies}/key-import-random.json" gw_curl "${endpoint}" -H "Authorization: unknown-$(openssl rand -hex 8)")"
  missing_code="$(code_of "${bodies}/key-import-missing.json" gw_curl "${endpoint}")"
  # Informational: whether the gateway stores keys hashed, as it reports itself to the
  # Dashboard. It comes from the image's tyk.conf, not from this bundle.
  hash_keys=unknown
  node_path="$(dashboard_get /api/system/nodes 2>/dev/null | jq -r '[.data.nodes[]? | select(.hostname == "__GATEWAY_HOSTNAME__")][0] | select(. != null) | "\(.id)/\(.hostname)"' 2>/dev/null || true)"
  if [[ -n "${node_path}" ]]; then
    hash_keys="$(dashboard_get "/api/system/node/${node_path}" 2>/dev/null | jq -r '.data.hash_keys // "unknown"' 2>/dev/null || echo unknown)"
  fi
  detail="imported values accepted: ${accepted_codes% }; random key: ${random_code}; no key: ${missing_code}; aliases read back: ${aliases_ok}; gateway hash_keys: ${hash_keys}"
  if ${accepted_ok} && [[ -n "${accepted_codes}" && "${random_code}" == 403 && "${missing_code}" == 401 ]] && ${aliases_ok}; then
    record KEY-IMPORT-001 passed "${detail}"
  else
    record KEY-IMPORT-001 failed "${detail}"
  fi
fi

if module_enabled versioning; then
  # The base serves v1 and a request header selects the child version v2. The Dashboard
  # decides the header (info.versioning.location and .key on the base), so it is read back.
  versioned="${GATEWAY_URL}/demo/__SLUG__/versioned/v1/echo"
  versioning_file="${bodies}/version-base-versioning.json"
  dashboard_get /api/apis/oas/__SLUG__-versioned 2>/dev/null | jq '."x-tyk-api-gateway".info.versioning // {}' >"${versioning_file}" 2>/dev/null \
    || echo '{}' >"${versioning_file}"
  linked=false
  jq -e '.enabled == true and .name == "v1" and .location == "header" and (.key // "" | length > 0)
         and ([.versions[]? | select(.name == "v2" and .id == "__SLUG__-versioned-v2")] | length == 1)' \
    "${versioning_file}" >/dev/null 2>&1 && linked=true
  version_key="$(jq -r '.key // "x-api-version"' "${versioning_file}" 2>/dev/null || echo x-api-version)"
  default_code="$(retry_code 200 20 "${bodies}/version-default.json" gw_curl "${versioned}")"
  default_v1="$(jq -r '.headers["x-service-version"] == "v1"' "${bodies}/version-default.json" 2>/dev/null || echo false)"
  v2_code="$(retry_code 200 20 "${bodies}/version-v2.json" gw_curl "${versioned}" -H "${version_key}: v2")"
  served_v2="$(jq -r '.headers["x-service-version"] == "v2"' "${bodies}/version-v2.json" 2>/dev/null || echo false)"
  unknown_code="$(code_of "${bodies}/version-unknown.json" gw_curl "${versioned}" -H "${version_key}: v9")"
  # One Dashboard row per API id, and none without an id: an update of a versioned base
  # without info.dbId inserts a ghost row with an empty id.
  dashboard_get '/api/apis?p=-1' 2>/dev/null \
    | jq '[.apis[]? | {id: (.api_definition.id // .id // null), api_id: (.api_definition.api_id // .api_model.api_id // .api_id // null)}]' \
      >"${bodies}/version-api-rows.json" 2>/dev/null || echo '[]' >"${bodies}/version-api-rows.json"
  rows_ok=false
  jq -e '(length > 0) and ([.[] | select(.id == null or .id == "")] | length == 0)
         and ([.[] | select(.api_id == "__SLUG__-versioned")] | length == 1)
         and ([.[] | select(.api_id == "__SLUG__-versioned-v2")] | length == 1)' \
    "${bodies}/version-api-rows.json" >/dev/null 2>&1 && rows_ok=true
  detail="no version header: ${default_code} served by v1: ${default_v1}; ${version_key}: v2: ${v2_code} served by v2: ${served_v2}; unknown version v9: ${unknown_code}; base readback links v2 by header ${version_key}: ${linked}; one row per API id and no row without an id: ${rows_ok}"
  if [[ "${default_code}" == 200 && "${default_v1}" == true && "${v2_code}" == 200 && "${served_v2}" == true \
    && "${unknown_code}" != 200 && "${linked}" == true && "${rows_ok}" == true ]]; then
    record VERSION-001 passed "${detail}"
  else
    record VERSION-001 failed "${detail}"
  fi
fi

if module_enabled vault; then
  # The upstream reports a SHA-256 of the credential it received, so the value itself is never
  # printed, compared in an argument or written to evidence. Secrets reach grep through a
  # process substitution and openssl on stdin.
  endpoint="${GATEWAY_URL}/demo/__SLUG__/vault-credentials/v1/echo"
  vault_value() { vault_api GET "__VAULT_DATA_PATH__" 2>/dev/null | jq -r '.data.data.__VAULT_SECRET_FIELD__ // empty' 2>/dev/null || true; }
  sha256_of() { printf '%s' "$1" | openssl dgst -sha256 -r | cut -d' ' -f1; }
  upstream_hash() { jq -r '.headers["x-upstream-credential-sha256"] // empty' "$1" 2>/dev/null || true; }
  current="$(vault_value)"
  if [[ -z "${current}" ]]; then
    record SECRETS-VAULT-001 failed "Vault holds no upstream credential at __VAULT_SECRET_PATH__ (dev mode keeps data in memory); run scripts/configure-demo.sh"
  else
    first_code="$(retry_code 200 20 "${bodies}/vault-injected.json" gw_curl "${endpoint}" -D "${bodies}/vault-injected-headers.txt")"
    injected=false
    [[ "$(upstream_hash "${bodies}/vault-injected.json")" == "$(sha256_of "${current}")" ]] && injected=true
    forged_code="$(code_of "${bodies}/vault-forged.json" gw_curl "${endpoint}" -H 'X-Upstream-Credential: supplied-by-the-client')"
    overwritten=false
    [[ "${forged_code}" == 200 && "$(upstream_hash "${bodies}/vault-forged.json")" == "$(sha256_of "${current}")" ]] && overwritten=true
    client_blind=true
    for file in vault-injected.json vault-injected-headers.txt vault-forged.json; do
      grep -F -f <(printf '%s\n' "${current}") "${bodies}/${file}" >/dev/null 2>&1 && client_blind=false
    done
    # Rotate in Vault, then send exactly one request: no API change, no reload, no retry.
    rotated="$(openssl rand -hex 24)"
    rotate_code="$(rotated_value="${rotated}" jq -nc '{data: {__VAULT_SECRET_FIELD__: env.rotated_value}}' \
      | vault_api POST "__VAULT_DATA_PATH__" -H 'Content-Type: application/json' --data-binary @- -o /dev/null -w '%{http_code}' 2>/dev/null || true)"
    rotated_code="$(code_of "${bodies}/vault-rotated.json" gw_curl "${endpoint}")"
    rotated_ok=false
    [[ "${rotated_code}" == 200 && "$(vault_value)" == "${rotated}" && "$(upstream_hash "${bodies}/vault-rotated.json")" == "$(sha256_of "${rotated}")" ]] && rotated_ok=true
    grep -F -f <(printf '%s\n' "${rotated}") "${bodies}/vault-rotated.json" >/dev/null 2>&1 && client_blind=false
    detail="first request ${first_code}, upstream received the Vault value: ${injected}; client-supplied X-Upstream-Credential overwritten: ${overwritten}; value absent from every client response: ${client_blind}; Vault rotation (HTTP ${rotate_code:-000}) carried by the next request with no API change or reload: ${rotated_ok}"
    if [[ "${first_code}" == 200 && "${injected}" == true && "${overwritten}" == true && "${client_blind}" == true && "${rotated_ok}" == true ]]; then
      record SECRETS-VAULT-001 passed "${detail}"
    else
      record SECRETS-VAULT-001 failed "${detail}"
    fi
  fi
fi

if module_enabled upstream-mtls; then
  api_file="${ROOT_DIR}/apis/__SLUG__-upstream-mtls.oas.json"
  cert_dir="${ROOT_DIR}/generated/certs/upstream"
  if [[ -z "${UPSTREAM_MTLS_CLIENT_CERT_ID:-}" || -z "${UPSTREAM_MTLS_DECOY_PIN_ID:-}" || ! -f "${api_file}" ]]; then
    record UPSTREAM-MTLS-001 blocked "Upstream mTLS certificates are not stored; run scripts/configure-demo.sh"
  else
    endpoint="${GATEWAY_URL}/demo/__SLUG__/upstream-mtls/v1/echo"
    put_upstream_api() {
      curl -fsS -X PUT "${DASHBOARD_URL}/api/apis/oas/__SLUG__-upstream-mtls" -H "authorization: ${DASHBOARD_API_KEY}" \
        -H 'Content-Type: application/json' --data-binary @- >/dev/null 2>&1
    }
    pinned_code="$(retry_code 200 30 "${bodies}/upstream-mtls-pinned.json" gw_curl "${endpoint}")"
    client_cn="$(jq -r '.client_cn // empty' "${bodies}/upstream-mtls-pinned.json" 2>/dev/null || true)"
    # Pin another public key that IS in the certificate store (an unknown ID would mean no
    # pinning at all): the gateway must refuse to proxy. Then restore the generated pin.
    jq --arg pin "${UPSTREAM_MTLS_DECOY_PIN_ID}" '."x-tyk-api-gateway".upstream.certificatePinning.domainToPublicKeysMapping[0].publicKeys = [$pin]' \
      "${api_file}" | put_upstream_api || true
    wrong_code="$(retry_code 500 30 "${bodies}/upstream-mtls-wrong-pin.json" gw_curl "${endpoint}")"
    wrong_message="$(jq -r '.error == "There was a problem proxying the request"' "${bodies}/upstream-mtls-wrong-pin.json" 2>/dev/null || echo false)"
    put_upstream_api <"${api_file}" || true
    restored_code="$(retry_code 200 30 "${bodies}/upstream-mtls-restored.json" gw_curl "${endpoint}")"
    # The upstream itself, from the host: refused without a client certificate, served with one.
    direct_without="$(code_of "${bodies}/upstream-mtls-direct-no-cert.txt" curl --cacert "${cert_dir}/ca.crt" "https://127.0.0.1:${UPSTREAM_MTLS_PORT}/v1/echo")"
    direct_with="$(code_of "${bodies}/upstream-mtls-direct-with-cert.json" curl --cacert "${cert_dir}/ca.crt" \
      --cert "${cert_dir}/gateway-client.crt" --key "${cert_dir}/gateway-client.key" "https://127.0.0.1:${UPSTREAM_MTLS_PORT}/v1/echo")"
    detail="correct pin: ${pinned_code} (upstream saw client certificate '${client_cn}'); pin swapped for another stored public key: ${wrong_code} (proxy error body: ${wrong_message}); pin restored: ${restored_code}; upstream direct without a client certificate: ${direct_without}, with one: ${direct_with}"
    if [[ "${pinned_code}" == 200 && "${client_cn}" == "__DISPLAY_NAME__ Gateway" && "${wrong_code}" == 500 && "${wrong_message}" == true \
      && "${restored_code}" == 200 && "${direct_without}" != 200 && "${direct_with}" == 200 ]]; then
      record UPSTREAM-MTLS-001 passed "${detail}"
    else
      record UPSTREAM-MTLS-001 failed "${detail}"
    fi
  fi
fi

if module_enabled portal; then
  # portal_read <path> <fallback-json>: a Portal error yields the fallback, so PORTAL-001
  # records failed and the checks after it still run.
  portal_read() {
    curl -fsS "${PORTAL_URL}/portal-api$1" -H "Authorization: ${PORTAL_API_TOKEN:-}" 2>/dev/null || printf '%s' "$2"
  }
  providers="$(portal_read '/providers?per_page=100' '[]')"
  products="$(portal_read '/products?per_page=100' '[]')"
  plans="$(portal_read '/plans?per_page=100' '[]')"
  catalogues="$(portal_read '/catalogues?per_page=100' '[]')"
  users="$(portal_read '/users?per_page=100' '[]')"
  pages="$(portal_read '/pages?per_page=100' '[]')"
  public_catalogue_id="$(jq -r '.[]? | select(.NameWithSlug == "__SLUG__-public-catalogue" and .VisibilityStatus == "Public") | .ID' <<<"${catalogues}" 2>/dev/null | head -1 || true)"
  documentation_page_id="$(jq -r '.[]? | select(.Path == "/docs/__SLUG__-api" and .Status == "Published") | .ID' <<<"${pages}" 2>/dev/null | head -1 || true)"
  public_catalogue='{}'
  documentation_blocks='[]'
  [[ -z "${public_catalogue_id}" ]] || public_catalogue="$(portal_read "/catalogues/${public_catalogue_id}" '{}')"
  [[ -z "${documentation_page_id}" ]] || documentation_blocks="$(portal_read "/pages/${documentation_page_id}/content-blocks?per_page=100" '[]')"
  if [[ "$(jq 'length' <<<"${providers}")" -gt 0 && "$(jq 'length' <<<"${products}")" -gt 0 && "$(jq 'length' <<<"${plans}")" -gt 0 && "$(jq 'length' <<<"${catalogues}")" -gt 0 && "$(jq 'length' <<<"${users}")" -gt 0 ]] \
    && jq -e '(.Products | length) > 0 and (.Plans | length) > 0' <<<"${public_catalogue}" >/dev/null \
    && jq -e '[.[] | select(.MarkdownEnabled == true and (.MarkdownContent | length > 0))] | length >= 3' <<<"${documentation_blocks}" >/dev/null; then
    record PORTAL-001 passed "Portal API proves provider, product, plan, public catalogue, user and published documentation"
  else
    record PORTAL-001 failed "Portal bootstrap data is incomplete"
  fi
fi

if module_enabled portal-branding; then
  brand_file="${ROOT_DIR}/portal/brand/brand.json"
  themes="$(curl -fsS "${PORTAL_URL}/portal-api/themes?per_page=100" -H "Authorization: ${PORTAL_API_TOKEN:-}" 2>/dev/null || echo '[]')"
  if ! jq -e '.[]? | select((.Name == "neutral-demo" or .ID == "neutral-demo") and .Status == "Current")' <<<"${themes}" >/dev/null 2>&1; then
    record PORTAL-UI-001 failed "The branded Portal theme is not active; run portal/apply-neutral-theme.sh"
  elif ! jq -e 'type == "object"' "${brand_file}" >/dev/null 2>&1; then
    record PORTAL-UI-001 failed "portal/brand/brand.json is missing or unreadable"
  else
    # The brand must be visible on the rendered pages, not merely uploaded: the brand logo and
    # favicon on each, none of the stock Tyk images or copy, and the brand colour in the overlay.
    brand_logo="$(jq -r '.logo_on_dark // .logo // empty' "${brand_file}")"
    brand_favicon="$(jq -r '.favicon // empty' "${brand_file}")"
    brand_primary="$(jq -r '.colors.primary // empty' "${brand_file}")"
    brand_issues=()
    for page_path in / /about-us /portal/catalogue-products /auth/password/login; do
      page="$(curl -fsS "${PORTAL_URL%/}${page_path}" 2>/dev/null || true)"
      if [[ -z "${page}" ]]; then
        brand_issues+=("${page_path}: not served")
        continue
      fi
      [[ -n "${brand_logo}" && "${page}" == *"/assets/images/brand/${brand_logo}"* ]] || brand_issues+=("${page_path}: no brand logo")
      [[ -n "${brand_favicon}" && "${page}" == *"rel=\"icon\" href=\"/assets/images/brand/${brand_favicon}\""* ]] || brand_issues+=("${page_path}: no brand favicon")
      for stock in tyk-portal-silhouette.png new-logo.svg dev-portal-logo.svg card-placeholder.svg about-us.png request-access.png \
        /assets/images/login.svg /assets/images/blog.svg /assets/images/not-found.svg /partners/ "Powered by © Tyk" "Tyk Developer Portal" "DEVELOPER PORTAL</a>" \
        "About Tyk" "Welcome to Tyk" "Tyk Pronunciation" "with Tyk APIs"; do
        [[ "${page}" != *"${stock}"* ]] || brand_issues+=("${page_path}: stock '${stock}'")
      done
    done
    overlay="$(curl -fsS "${PORTAL_URL%/}/assets/stylesheets/neutral-overlay.css" 2>/dev/null || true)"
    [[ -n "${brand_primary}" && "${overlay}" == *"--tdp-primary: ${brand_primary};"* ]] || brand_issues+=("overlay lacks --tdp-primary ${brand_primary}")
    # A screenshot counts only when scripts/record-screenshot.sh registered it for this stack:
    # the file still has the recorded SHA-256, the page is this Portal's, the organisation is
    # this stack's and the brand kit is the one applied now. A file that merely exists proves
    # nothing about this run.
    shots=0
    shot_issues=()
    shot_manifest="${ROOT_DIR}/evidence/screenshots/manifest.json"
    shot_sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
    brand_digest="$(shot_sha "${brand_file}")"
    for shot in portal-theme portal-catalogue portal-openapi-viewer portal-login; do
      shot_file="${ROOT_DIR}/evidence/screenshots/${shot}.png"
      shot_entry="$(jq -c --arg name "${shot}" '.[$name] // empty' "${shot_manifest}" 2>/dev/null || true)"
      if [[ ! -s "${shot_file}" ]]; then
        shot_issues+=("${shot}: no capture")
      elif [[ -z "${shot_entry}" ]]; then
        shot_issues+=("${shot}: not registered with scripts/record-screenshot.sh")
      elif [[ "$(jq -r '.sha256' <<<"${shot_entry}")" != "$(shot_sha "${shot_file}")" ]]; then
        shot_issues+=("${shot}: file changed since it was registered")
      elif [[ "$(jq -r '.url' <<<"${shot_entry}")" != "${PORTAL_URL%/}/"* ]]; then
        shot_issues+=("${shot}: captured from another Portal")
      elif [[ "$(jq -r '.org_id' <<<"${shot_entry}")" != "${ORG_ID:-}" ]]; then
        shot_issues+=("${shot}: registered for another stack")
      elif [[ "$(jq -r '.brand_digest' <<<"${shot_entry}")" != "${brand_digest}" ]]; then
        shot_issues+=("${shot}: taken with a different brand kit")
      else
        shots=$((shots + 1))
      fi
    done
    if jq -e '.placeholder == true' "${brand_file}" >/dev/null 2>&1; then
      brand_label="neutral placeholder brand (no stock Tyk branding; supply your organisation's kit for a branded presentation)"
    else
      brand_label="your organisation's brand kit (logo, favicon, ${brand_primary} primary, no stock Tyk images or copy)"
    fi
    if [[ ${#brand_issues[@]} -gt 0 ]]; then
      record PORTAL-UI-001 failed "Brand not applied: ${brand_issues[*]}"
    elif [[ "${shots}" -lt 4 ]]; then
      record PORTAL-UI-001 blocked "Home, about, catalogue and login serve the ${brand_label}; ${shots}/4 browser screenshots registered for this stack (${shot_issues[*]}). Capture each page into evidence/screenshots/ and run scripts/record-screenshot.sh <name> <portal-path> <marker>"
    else
      record PORTAL-UI-001 passed "Home, about, catalogue and login serve the ${brand_label}; 4 browser screenshots registered for this stack and brand kit"
    fi
  fi
fi

if module_enabled portal-catalogues; then
  # Configuration side through portal-api, then the developer side: log in as one developer
  # per organisation and read the catalogue page each one is actually shown.
  catalogue_prerequisites=true
  for variable in PORTAL_API_TOKEN PORTAL_INTERNAL_CATALOGUE_ID PORTAL_PARTNER_CATALOGUE_ID PORTAL_SHARED_PRODUCT_ID PORTAL_PARTNER_PRODUCT_ID PORTAL_DOCS_PRODUCT_ID PORTAL_INTERNAL_DEVELOPER_PASSWORD PORTAL_PARTNER_DEVELOPER_PASSWORD; do
    [[ -n "${!variable:-}" ]] || catalogue_prerequisites=false
  done
  if [[ "${catalogue_prerequisites}" != true ]]; then
    record PORTAL-CAT-001 blocked "Portal catalogue runtime state missing; run scripts/configure-demo.sh"
  else
    papi() { curl -fsS "${PORTAL_URL%/}/portal-api$1" -H "Authorization: ${PORTAL_API_TOKEN}"; }
    catalogue_view() {
      jq -n --argjson c "$(papi "/catalogues/$1")" --argjson a "$(papi "/catalogues/$1/audiences?per_page=100")" \
        '{name: $c.Name, visibility: $c.VisibilityStatus, products: $c.Products, audience_teams: [$a[]? | .TeamID]}'
    }
    # Guarded: a Portal error here must leave an empty id (the mapping check then fails),
    # not abort the run before the remaining checks and results.json.
    public_catalogue_id="$(papi '/catalogues?per_page=100' 2>/dev/null | jq -r '.[]? | select(.NameWithSlug == "__SLUG__-public-catalogue") | .ID' 2>/dev/null | head -1 || true)"
    jq -n --argjson internal "$(catalogue_view "${PORTAL_INTERNAL_CATALOGUE_ID}")" \
          --argjson partner "$(catalogue_view "${PORTAL_PARTNER_CATALOGUE_ID}")" \
          --argjson public "$(catalogue_view "${public_catalogue_id}")" \
          --argjson docs "$(papi "/products/${PORTAL_DOCS_PRODUCT_ID}" | jq '{name: .Name, documentation_only: .IsDocumentationOnly}')" \
          --argjson specs "$(papi "/products/${PORTAL_DOCS_PRODUCT_ID}/spec-details?per_page=100" | jq '[.[]? | .OASUrl]')" \
          --arg shared "$(papi "/products/${PORTAL_SHARED_PRODUCT_ID}" | jq -r '.Name')" \
          --arg partner_product "$(papi "/products/${PORTAL_PARTNER_PRODUCT_ID}" | jq -r '.Name')" \
      '{internal: $internal, partner: $partner, public: $public, documentation_only: ($docs + {specs: $specs}),
        shared_product: $shared, partner_product: $partner_product}' >"${bodies}/portal-catalogue-mapping.json" 2>/dev/null \
      || echo '{}' >"${bodies}/portal-catalogue-mapping.json"
    mapping_ok=false
    # Catalogue readbacks list products by name.
    jq -e '
      .shared_product as $shared | .partner_product as $partner | .documentation_only.name as $docs
      | (.internal.visibility == "Private") and (.partner.visibility == "Private")
      and (.internal.audience_teams | length > 0) and (.partner.audience_teams | length > 0)
      and ((.internal.audience_teams - .partner.audience_teams) | length > 0)
      and (.internal.products | index($shared) != null and index($partner) == null)
      and (.partner.products | index($shared) != null and index($partner) != null and index($docs) != null)
      and ((.public.products // []) | index($partner) == null)
      and .documentation_only.documentation_only == true and (.documentation_only.specs | length > 0)' \
      "${bodies}/portal-catalogue-mapping.json" >/dev/null 2>&1 && mapping_ok=true
    shared_path="$(papi "/products/${PORTAL_SHARED_PRODUCT_ID}" | jq -r '.Path' || true)"
    partner_path="$(papi "/products/${PORTAL_PARTNER_PRODUCT_ID}" | jq -r '.Path' || true)"
    developer_view() {
      local email="$1" password="$2" jar page
      jar="$(mktemp)"
      # The password goes in on stdin (printf is a builtin), never on curl's command line,
      # where any local user could read it from the process list.
      printf '%s' "${password}" | curl -sS -c "${jar}" -b "${jar}" -o /dev/null -X POST "${PORTAL_URL%/}/auth/password/login" \
        --data-urlencode "login=${email}" --data-urlencode "password@-" || true
      page="$(curl -sS -b "${jar}" "${PORTAL_URL%/}/portal/catalogue-products" || true)"
      rm -f "${jar}"
      jq -n --arg email "${email}" --arg page "${page}" --arg shared "${shared_path}" --arg partner "${partner_path}" '{
          developer: $email,
          logged_in: ($page | test("Log out")),
          catalogues: [$page | scan("<option value=\"[0-9]+\" *(?:selected)? *>([^<]+)<") | .[0] | select(. != "All Catalogues")],
          sees_shared_product: ($page | contains("catalogue-products/" + $shared)),
          sees_partner_product: ($page | contains("catalogue-products/" + $partner))
        }'
    }
    jq -n --argjson internal "$(developer_view "${PORTAL_INTERNAL_DEVELOPER_EMAIL:-internal-developer@__SLUG__.invalid}" "${PORTAL_INTERNAL_DEVELOPER_PASSWORD}")" \
          --argjson partner "$(developer_view "${PORTAL_PARTNER_DEVELOPER_EMAIL:-partner-developer@__SLUG__.invalid}" "${PORTAL_PARTNER_DEVELOPER_PASSWORD}")" \
      '{internal: $internal, partner: $partner}' >"${bodies}/portal-developer-views.json" 2>/dev/null \
      || echo '{}' >"${bodies}/portal-developer-views.json"
    views_ok=false
    jq -e '
      .internal.logged_in and .partner.logged_in
      and (.internal.catalogues | index("__DISPLAY_NAME__ Internal Catalogue") != null and index("__DISPLAY_NAME__ Partner Catalogue") == null)
      and (.partner.catalogues | index("__DISPLAY_NAME__ Partner Catalogue") != null and index("__DISPLAY_NAME__ Internal Catalogue") == null)
      and .internal.sees_shared_product and .partner.sees_shared_product
      and (.internal.sees_partner_product | not) and .partner.sees_partner_product' \
      "${bodies}/portal-developer-views.json" >/dev/null 2>&1 && views_ok=true
    detail="catalogue mapping (internal: shared product; partner: shared, partner and documentation-only products; partner product not public; both private with audiences): ${mapping_ok}; developer views (internal developer sees the internal catalogue only and not the partner product; partner developer sees the partner catalogue and product): ${views_ok}"
    if ${mapping_ok} && ${views_ok}; then
      record PORTAL-CAT-001 passed "${detail}"
    else
      record PORTAL-CAT-001 failed "${detail}"
    fi
  fi
fi

if module_enabled dcr; then
  # portal/configure-dcr.sh triggers the Portal DCR events and snapshots the JWT API after
  # each; this asserts every snapshot and the live definition. Portal 1.17 and 1.18 dropped
  # allowedAudiences and allowedIssuers and zeroed the JWKS cacheTimeout on these events.
  events_file="${ROOT_DIR}/evidence/portal-dcr-events.json"
  if [[ "${identity_provider_mode}" == external ]]; then
    record PORTAL-DCR-AUD-001 blocked "External identity provider: Portal DCR wiring and the audience check need provider settings"
  elif [[ ! -f "${events_file}" ]]; then
    record PORTAL-DCR-AUD-001 blocked "No DCR event snapshots; run scripts/configure-demo.sh (portal/configure-dcr.sh)"
  elif [[ "$(jq -r '.org_id // empty' "${events_file}" 2>/dev/null)" != "${ORG_ID:-}" ]]; then
    record PORTAL-DCR-AUD-001 blocked "DCR event snapshots belong to another stack; rerun portal/configure-dcr.sh"
  else
    intact='(.allowedAudiences | length > 0) and (.allowedIssuers | length > 0) and (.jwksCacheTimeouts | length > 0) and all(.jwksCacheTimeouts[]; . != "0s" and . != "" and . != null)'
    live="$(dashboard_get /api/apis/oas/__SLUG__-jwt | jq -c '."x-tyk-api-gateway".server.authentication.securitySchemes.jwtAuth
      | {allowedAudiences: (.allowedAudiences // []), allowedIssuers: (.allowedIssuers // []), jwksCacheTimeouts: [(.jwksURIs // [])[] | .cacheTimeout]}' 2>/dev/null || echo '{}')"
    printf '%s\n' "${live}" >"${bodies}/portal-dcr-jwt-live.json"
    events="$(jq -r '[.events[]?.event] | join(", ")' "${events_file}")"
    has_save="$(jq -r '[.events[]?.event] | any(. == "dcr-enable" or . == "product-save-with-dcr")' "${events_file}")"
    has_approval="$(jq -r '[.events[]?.event] | any(. == "access-request-approval")' "${events_file}")"
    approval_supported="$(jq -r '.approval_supported // true' "${events_file}")"
    save_rewrote="$(jq -r '[.events[]? | select(.event == "dcr-enable" or .event == "product-save-with-dcr") | .portal_rewrite_detected] | any' "${events_file}")"
    events_intact="$(jq -r "[.events[]? | (${intact})] | all" "${events_file}")"
    live_intact="$(jq -r "try (${intact}) catch false" <<<"${live}")"
    versions="Portal $(jq -r '.portal_version' "${events_file}") + Dashboard $(jq -r '.dashboard_version' "${events_file}")"
    if [[ "${has_save}" == true && ( "${events_intact}" != true || "${live_intact}" != true ) ]]; then
      record PORTAL-DCR-AUD-001 failed "${versions} stripped JWT validation on a DCR event (${events}): snapshots intact ${events_intact}, live intact ${live_intact}. Re-import apis/ after every DCR event, or move to Portal 1.19.0 with Dashboard 5.15.0."
    elif [[ "${has_save}" != true || "${save_rewrote}" != true ]]; then
      record PORTAL-DCR-AUD-001 blocked "The DCR product save was not captured or did not reach the JWT API (events: ${events:-none}); rerun portal/configure-dcr.sh"
    elif [[ "${has_approval}" != true ]]; then
      if [[ "${approval_supported}" == true ]]; then
        record PORTAL-DCR-AUD-001 blocked "Only these DCR events were captured: ${events}; rerun portal/configure-dcr.sh"
      else
        record PORTAL-DCR-AUD-001 blocked "${versions}: the product save kept audiences, issuers and the JWKS cache; the approval event needs the Portal 1.19.0 admin provisioning endpoints"
      fi
    else
      record PORTAL-DCR-AUD-001 passed "${versions}: the DCR product save rewrote the JWT scheme and the access-request approval followed; allowedAudiences, allowedIssuers and the JWKS cacheTimeout survived every event (${events}) and the live definition is intact"
    fi
  fi
fi

if module_enabled governance; then
  if [[ "${GOVERNANCE_LICENSED:-}" == false ]]; then
    record GOV-001 blocked "Dashboard license lacks the governance claim"
  else
    legacy_api="__SLUG__-governed-legacy"
    standard_api="__SLUG__-governed-standard"
    governance_compliance() {
      curl -fsS "${DASHBOARD_URL}/api/governance/apis/${1}/compliance" \
        -H "authorization: ${DASHBOARD_API_KEY}" 2>/dev/null || printf '{}'
    }
    # Evaluation is triggered by the API save, the ruleset create, and the category change,
    # and takes one to two seconds for a small spec. Poll until both APIs carry a verdict
    # rather than reading an unassessed null and calling it a failure.
    legacy_compliance='{}'
    standard_compliance='{}'
    for _ in $(seq 1 60); do
      legacy_compliance="$(governance_compliance "${legacy_api}")"
      standard_compliance="$(governance_compliance "${standard_api}")"
      # `in_process` is a real, non-null status while the background evaluation runs, so wait
      # for a settled verdict on both APIs rather than for any status at all.
      if jq -e '.overall_status == "compliant" or .overall_status == "non_compliant"' <<<"${legacy_compliance}" >/dev/null 2>&1 \
        && jq -e '.overall_status == "compliant" or .overall_status == "non_compliant"' <<<"${standard_compliance}" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    legacy_details="$(curl -fsS "${DASHBOARD_URL}/api/governance/evaluation-details/${legacy_api}?p=-1" -H "authorization: ${DASHBOARD_API_KEY}" 2>/dev/null || printf '{}')"
    legacy_status="$(jq -r '.overall_status // "unassessed"' <<<"${legacy_compliance}")"
    standard_status="$(jq -r '.overall_status // "unassessed"' <<<"${standard_compliance}")"
    if [[ "${legacy_status}" == non_compliant && "${standard_status}" == compliant ]] \
      && jq -e '[.rulesets[]? | select(.error_count > 0)] | length > 0' <<<"${legacy_compliance}" >/dev/null \
      && jq -e '(.violations | length) > 0 and ([.violations[] | select((.how_to_fix // "") != "")] | length) == (.violations | length)' <<<"${legacy_details}" >/dev/null; then
      record GOV-001 passed "Legacy API non-compliant with how-to-fix violations; standard API compliant"
    else
      record GOV-001 failed "Compliance proof failed legacy=${legacy_status} standard=${standard_status}"
    fi

    # The gate itself is evidence: an unknown category returns HTTP 200 with zero rulesets
    # and zero issues, so a CI check that does not fail on an empty ruleset list passes
    # forever. The contract is 0 compliant, 1 warnings only, 2 non-compliant, 3 could not
    # evaluate, so the unknown category must produce 3 rather than a silent pass.
    #
    # Exit 1 is accepted for the standard spec because the sandbox lints the local file,
    # which carries no Dashboard-injected server entry: any remaining findings are
    # warn-severity OWASP advisories from other rules, and warnings are not a failure.
    governance_check="${ROOT_DIR}/governance/governance-check.sh"
    run_governance_check() {
      env TYK_DASHBOARD_URL="${DASHBOARD_URL}" TYK_DASHBOARD_TOKEN="${DASHBOARD_API_KEY}" \
        "${governance_check}" "$@" >/dev/null 2>&1
    }
    legacy_exit=0
    run_governance_check "${ROOT_DIR}/apis/${legacy_api}.oas.json" --categories __SLUG__-payments --api-id ci-legacy || legacy_exit=$?
    standard_exit=0
    run_governance_check "${ROOT_DIR}/apis/${standard_api}.oas.json" --categories __SLUG__-payments --api-id ci-standard || standard_exit=$?
    unknown_exit=0
    run_governance_check "${ROOT_DIR}/apis/${standard_api}.oas.json" --categories no-such-category --api-id ci-unknown || unknown_exit=$?
    if [[ "${legacy_exit}" == 2 && "${unknown_exit}" == 3 ]] && [[ "${standard_exit}" == 0 || "${standard_exit}" == 1 ]]; then
      record GOV-002 passed "CI gate exits legacy=${legacy_exit} standard=${standard_exit} unknown-category=${unknown_exit}"
    else
      record GOV-002 failed "CI gate exits legacy=${legacy_exit} standard=${standard_exit} unknown-category=${unknown_exit}"
    fi
  fi
fi

# Operator proofs need a Kubernetes cluster, so they run out of band and leave evidence files
# behind: operator/verify-reconciliation.sh (called by apply-operator.sh) writes OPERATOR-001
# and operator/verify-governance.sh writes GOV-003. Evidence counts only when it belongs to this
# stack: its org_id must be this bundle's ORG_ID and the object it names must still exist in
# this Dashboard, so a pass left behind by a purged or different stack is never reported.
# record_evidence_file <test-id> <file> <how-to-run> <jq-id-path> <dashboard-path-prefix>
record_evidence_file() {
  local test_id="$1" file="$2" how_to_run="$3" id_path="$4" live_prefix="$5"
  local evidence proof status detail org object_id code
  local stale="Operator evidence is from a different stack; re-run: ${how_to_run}"
  if [[ ! -f "${file}" ]]; then
    record "${test_id}" blocked "${how_to_run}"
    return
  fi
  if ! evidence="$(jq -c 'select(type == "object")' "${file}" 2>/dev/null)" || [[ -z "${evidence}" ]]; then
    record "${test_id}" blocked "Unreadable evidence in evidence/$(basename "${file}"); re-run: ${how_to_run}"
    return
  fi
  proof="$(jq -r '.proof // empty' <<<"${evidence}")"
  status="$(jq -r '.status // empty' <<<"${evidence}")"
  detail="$(jq -r '.detail // .reason // empty' <<<"${evidence}")"
  org="$(jq -r '.org_id // empty' <<<"${evidence}")"
  if [[ "${proof}" != "${test_id}" ]] || [[ "${status}" != passed && "${status}" != failed ]]; then
    record "${test_id}" blocked "Unrecognised evidence in evidence/$(basename "${file}"); re-run: ${how_to_run}"
    return
  fi
  if [[ -z "${org}" || "${org}" != "${ORG_ID:-}" ]]; then
    record "${test_id}" blocked "${stale}"
    return
  fi
  if [[ "${status}" == passed ]]; then
    object_id="$(jq -r "${id_path} // empty" <<<"${evidence}")"
    code=000
    if [[ -n "${object_id}" ]]; then
      code="$(curl -sS -o /dev/null -w '%{http_code}' "${DASHBOARD_URL}${live_prefix}${object_id}" \
        -H "authorization: ${DASHBOARD_API_KEY:-}" 2>/dev/null || true)"
    fi
    if [[ "${code}" != 200 ]]; then
      record "${test_id}" blocked "${stale}"
      return
    fi
  fi
  record "${test_id}" "${status}" "${detail:-Recorded in evidence/$(basename "${file}")}"
}

if module_enabled operator; then
  record_evidence_file OPERATOR-001 "${ROOT_DIR}/evidence/operator-reconciliation.json" \
    "Run operator/install-operator-kind.sh, then source operator/operator-env.sh and ./operator/apply-operator.sh" \
    '.api.id' /api/apis/oas/
  if module_enabled governance; then
    record_evidence_file GOV-003 "${ROOT_DIR}/evidence/operator-governance.json" \
      "Run operator/verify-governance.sh after apply-operator.sh" \
      '.ruleset.id' /api/rulesets/
  fi
fi

# Tyk Sync delivery runs out of band (ci/tyk-sync-local.sh here, ci/tyk-sync.sh in CI) and
# leaves .runtime/tyk-sync/latest-promotion.json. SYNC-001 counts it only for this stack: its
# org_id must be this bundle's ORG_ID, its read-back must hold every API id in apis/ in that
# organisation, and each of those APIs must still exist in this Dashboard.
sync_status=blocked
if module_enabled tyk-sync; then
  sync_how="run ./ci/tyk-sync-local.sh promote apis (or ci/tyk-sync.sh in CI) against this stack"
  sync_summary="${ROOT_DIR}/.runtime/tyk-sync/latest-promotion.json"
  sync_id="$(jq -r '.promotion_id // empty' "${sync_summary}" 2>/dev/null || true)"
  sync_org="$(jq -r '.org_id // empty' "${sync_summary}" 2>/dev/null || true)"
  sync_evidence="${ROOT_DIR}/.runtime/tyk-sync/evidence/${sync_id}"
  if [[ ! -f "${sync_summary}" ]]; then
    record SYNC-001 blocked "No Tyk Sync promotion has been recorded; ${sync_how}"
  elif [[ -z "${sync_id}" || ! -d "${sync_evidence}" ]]; then
    record SYNC-001 blocked "The latest promotion record has no read-back evidence; ${sync_how}"
  elif [[ "${sync_org}" != "${ORG_ID:-}" ]]; then
    record SYNC-001 blocked "The latest promotion targeted another organisation, not this stack; ${sync_how}"
  else
    sync_missing=()
    sync_gone=()
    sync_count=0
    while IFS= read -r sync_api; do
      [[ -n "${sync_api}" ]] || continue
      sync_count=$((sync_count + 1))
      # Read-back files are dumps ({"oas": ...}); orgId must match where the dump records one.
      if ! jq -se --arg id "${sync_api}" --arg org "${ORG_ID}" \
        'any(.[]; ((.oas // .)["x-tyk-api-gateway"].info // {}) as $info | $info.id == $id and (($info.orgId // $org) == $org))' \
        "${sync_evidence}"/*.json >/dev/null 2>&1; then
        sync_missing+=("${sync_api}")
      fi
      code="$(curl -sS -o /dev/null -w '%{http_code}' "${DASHBOARD_URL}/api/apis/oas/${sync_api}" \
        -H "authorization: ${DASHBOARD_API_KEY:-}" 2>/dev/null || true)"
      [[ "${code}" == 200 ]] || sync_gone+=("${sync_api}")
    done < <(for file in "${ROOT_DIR}"/apis/*.oas.json; do [[ -e "${file}" ]] && jq -r '."x-tyk-api-gateway".info.id // empty' "${file}"; done | sort -u)
    # The promotion records a digest of every definition it delivered (ci/tyk-sync.sh,
    # definition_digest). An API file edited since then, even under the same id, means the
    # Dashboard holds content the bundle no longer describes, so the row needs a new promotion.
    sync_changed=()
    for sync_file in "${ROOT_DIR}"/apis/*.oas.json; do
      [[ -e "${sync_file}" ]] || continue
      sync_file_id="$(jq -r '."x-tyk-api-gateway".info.id // empty' "${sync_file}")"
      [[ -n "${sync_file_id}" ]] || continue
      if command -v sha256sum >/dev/null 2>&1; then
        sync_now="$(jq -S -c '(.oas // .)' "${sync_file}" | sha256sum | cut -d' ' -f1)"
      else
        sync_now="$(jq -S -c '(.oas // .)' "${sync_file}" | shasum -a 256 | cut -d' ' -f1)"
      fi
      sync_then="$(jq -r --arg id "${sync_file_id}" '.api_digests[$id] // empty' "${sync_summary}" 2>/dev/null || true)"
      [[ "${sync_now}" == "${sync_then}" ]] || sync_changed+=("${sync_file_id}")
    done
    sync_detail="$(jq -r '"\(.operation) \(.promotion_id) at \(.recorded_at): created \(.created), updated \(.updated)"' "${sync_summary}" 2>/dev/null || echo "${sync_id}")"
    if [[ "${sync_count}" -eq 0 ]]; then
      record SYNC-001 blocked "apis/ holds no Tyk OAS definitions to promote"
    elif [[ ${#sync_changed[@]} -gt 0 ]]; then
      record SYNC-001 blocked "The latest delivery (${sync_id}) does not match apis/ for ${sync_changed[*]}: the file changed since, or a rollback restored older content; ${sync_how}"
    elif [[ ${#sync_missing[@]} -gt 0 ]]; then
      record SYNC-001 blocked "Promotion ${sync_id} predates API(s) now in apis/ (${sync_missing[*]}); ${sync_how}"
    elif [[ ${#sync_gone[@]} -gt 0 ]]; then
      sync_status=failed
      record SYNC-001 failed "Promoted API(s) no longer in this Dashboard: ${sync_gone[*]} (${sync_detail})"
    else
      sync_status=passed
      record SYNC-001 passed "Tyk Sync ${sync_detail}; the Dashboard read-back holds all ${sync_count} owned APIs in this organisation with the delivered name, state, listen path, upstream, authentication, tags and operations, each is live, and apis/ is unchanged since"
    fi
  fi
fi

# CICD-001: the CI templates are checked statically and the validate step is run here. The
# delivery step they call is ci/tyk-sync.sh itself, so the row passes only once SYNC-001 has
# proved that step against this stack. The CI runners themselves are not exercised.
if module_enabled cicd; then
  ci_issues=()
  "${ROOT_DIR}/ci/tyk-sync.sh" validate "${ROOT_DIR}/apis" >/dev/null 2>&1 || ci_issues+=("ci/tyk-sync.sh validate apis failed")
  github_workflow="${ROOT_DIR}/ci/github/tyk-sync.yml"
  gitlab_pipeline="${ROOT_DIR}/ci/gitlab/tyk-sync.yml"
  for expected in 'ci/tyk-sync.sh validate' 'ci/tyk-sync.sh "${{ inputs.operation }}"' 'workflow_dispatch' 'environment:'; do
    grep -Fq -- "${expected}" "${github_workflow}" 2>/dev/null || ci_issues+=("GitHub workflow lacks ${expected}")
  done
  for expected in 'ci/tyk-sync.sh validate' 'ci/tyk-sync.sh promote' 'ci/tyk-sync.sh rollback' 'when: manual'; do
    grep -Fq -- "${expected}" "${gitlab_pipeline}" 2>/dev/null || ci_issues+=("GitLab pipeline lacks ${expected}")
  done
  if [[ ${#ci_issues[@]} -gt 0 ]]; then
    record CICD-001 failed "CI templates: $(IFS=';'; echo "${ci_issues[*]}")"
  elif [[ "${sync_status}" != passed ]]; then
    record CICD-001 blocked "CI templates call ci/tyk-sync.sh with manual promote and rollback gates and the validate step passes; the promote step they run is not yet proven against this stack (SYNC-001 ${sync_status})"
  else
    record CICD-001 passed "CI template checks and local promotion: the validate step passes on apis/, the GitHub and GitLab templates call ci/tyk-sync.sh with manual promote and rollback gates, and that promote step passed locally against this stack (SYNC-001). No CI runner ran: acceptance of a real pipeline needs its own run, change and rollback evidence"
  fi
fi

if module_enabled mcp; then
  # One remote proxy proves four things: authentication is enforced, a blocked
  # tool is invisible to discovery, calling it anyway is refused, and a per-tool
  # rate limit bites without touching a tool that has no limit.
  mcp_url="${GATEWAY_URL}/demo/__SLUG__/mcp-broker/mcp"

  # mcp-check.py exits non-zero on any 4xx. Here a 4xx is the expected result
  # rather than a script failure, so the exit code is swallowed and the JSON
  # body parsed separately. The transport flags are fixed when the bundle is
  # generated, for the same reason gw_curl exists.
__MCP_JSON_FN__
  mcp_status() {
    local body="${1:-}"
    [[ -n "${body}" ]] || body='{}'
    jq -r '.status // 0' <<<"${body}" 2>/dev/null || echo 0
  }

  anon_status="$(mcp_status "$(mcp_json probe "${mcp_url}")")"
  if [[ "${anon_status}" == 401 ]]; then
    record MCP-AUTH-001 passed "Unauthenticated MCP initialize rejected with 401"
  else
    record MCP-AUTH-001 failed "Unauthenticated MCP initialize returned ${anon_status}"
  fi

  if [[ -n "${DEMO_MCP_KEY:-}" ]]; then
    listed="$(mcp_json list "${mcp_url}" --key "${DEMO_MCP_KEY}")"
    tool_count="$(jq -r '.count // 0' <<<"${listed}" 2>/dev/null || echo 0)"
    has_blocked="$(jq -r '[.tools[]? | select(. == "delete_user")] | length' <<<"${listed}" 2>/dev/null || echo 0)"
    if [[ "${tool_count}" -gt 0 && "${has_blocked}" == 0 ]]; then
      record MCP-DISCOVERY-001 passed "Keyed tools/list returned ${tool_count} tools; blocked tool absent from discovery"
    else
      record MCP-DISCOVERY-001 failed "tools/list count=${tool_count} blocked_tool_present=${has_blocked}"
    fi

    blocked_status="$(mcp_status "$(mcp_json call "${mcp_url}" delete_user --args '{"id":"1"}' --key "${DEMO_MCP_KEY}")")"
    if [[ "${blocked_status}" == 403 ]]; then
      record MCP-BLOCK-001 passed "Calling the blocked tool directly returned 403"
    else
      record MCP-BLOCK-001 failed "Blocked tool call returned ${blocked_status}"
    fi

    # The limit is 1 per 5s, so let the window reset before measuring.
    sleep 6
    create_args='{"name":"verify","email":"verify@example.test"}'
    first="$(mcp_status "$(mcp_json call "${mcp_url}" create_user --args "${create_args}" --key "${DEMO_MCP_KEY}")")"
    second="$(mcp_status "$(mcp_json call "${mcp_url}" create_user --args "${create_args}" --key "${DEMO_MCP_KEY}")")"
    unlimited="$(mcp_status "$(mcp_json call "${mcp_url}" generate_uuid --key "${DEMO_MCP_KEY}")")"
    if [[ "${first}" == 200 && "${second}" == 429 && "${unlimited}" == 200 ]]; then
      record MCP-LIMIT-001 passed "Per-tool limit: create_user 200 then 429; unlimited tool still 200"
    else
      record MCP-LIMIT-001 failed "Per-tool limit: create_user ${first}/${second}, unlimited ${unlimited}"
    fi

    # REST-as-MCP: the proxy's upstream is tyk://<rest-api-id>/mcp, so the
    # gateway derives tools from the REST API's own OAS operations. Proving a
    # non-empty tool list proves the derivation ran.
    rest_mcp_url="${GATEWAY_URL}/demo/__SLUG__/rest-as-mcp/mcp"
    rest_listed="$(mcp_json list "${rest_mcp_url}" --key "${DEMO_MCP_KEY}")"
    rest_count="$(jq -r '.count // 0' <<<"${rest_listed}" 2>/dev/null || echo 0)"
    if [[ "${rest_count}" -gt 0 ]]; then
      record MCP-REST-001 passed "REST-as-MCP proxy derived ${rest_count} tool(s) from the REST API OAS"
    else
      record MCP-REST-001 failed "REST-as-MCP proxy exposed no tools; check the tyk:// upstream resolves to the REST API id"
    fi
  else
    for mcp_test in MCP-DISCOVERY-001 MCP-BLOCK-001 MCP-LIMIT-001 MCP-REST-001; do
      record "${mcp_test}" blocked "DEMO_MCP_KEY missing; run scripts/configure-demo.sh"
    done
  fi
fi

# finish_verification (the EXIT trap above) writes results.json, the note and the rows.
verify_completed=true
exit "${failed}"
