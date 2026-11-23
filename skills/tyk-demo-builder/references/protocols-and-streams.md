# WebSocket, GraphQL And Streams Modules

Last verified: 2026-11-19 against Tyk Gateway 5.16.0 and Dashboard 5.16.0.

This page covers what the protocol modules generate and what their proofs assert. For proxying
WebSocket and Server-Sent Events, Tyk Streams and Gateway events in general, use the
`tyk-websockets-streams-webhooks` skill.

| Module | What it generates | Proof and assertion |
| --- | --- | --- |
| `websocket` | a WebSocket echo upstream and `<display name> WebSocket API` on `/demo/<slug>/ws/`, key protected; WebSockets switched on in `config/gateway.env` | `WS-001`: an authenticated upgrade through the gateway, one message sent and the echo received (`scripts/websocket-check.py`) |
| `graphql` | a GraphQL upstream and `<display name> GraphQL API` on `/demo/<slug>/graphql/` | `GQL-001`: the query `{ hello }` returns `"hello through Tyk"` through the gateway |
| `streams` | a single-broker Kafka (`apache/kafka:3.8.0`) and `<display name> Streams Producer` (listen path `/demo/<slug>/streams/`; the proof posts to `events`) that publishes an HTTP request body to the topic `<slug>-events` | `STREAM-001`: creates the topic if missing, publishes an event with a unique id through the gateway, and finds that id in the topic |

`STREAM-001` retries the publish up to five times, two seconds apart, because the first request
after an API load or reload can find the stream still starting; it records which attempt
succeeded. A run that needs a retry is not a failure of the module.

The Kafka profile proves the Kafka path only. A requirement for another broker, authenticated
consumers or production throughput stays `talk-track` until that path is built and run.
