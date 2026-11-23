# syntax=docker/dockerfile:1
ARG TYK_GATEWAY_VERSION
FROM --platform=linux/amd64 tykio/tyk-plugin-compiler:${TYK_GATEWAY_VERSION} AS compiler

ARG TYK_GATEWAY_VERSION
ARG TYK_GATEWAY_ARCH=amd64
WORKDIR /plugin-source
COPY plugin.go ./
RUN case "${TYK_GATEWAY_ARCH}" in amd64|arm64) ;; *) exit 2 ;; esac \
    && /build.sh post_enrich.so "" linux "${TYK_GATEWAY_ARCH}" \
    && mkdir -p /plugin-output \
    && cp /plugin-source/post_enrich_*_linux_${TYK_GATEWAY_ARCH}.so /plugin-output/post_enrich_${TYK_GATEWAY_VERSION}_linux_${TYK_GATEWAY_ARCH}.so \
    && cp /plugin-source/post_enrich_*_linux_${TYK_GATEWAY_ARCH}.so /plugin-output/post_enrich_${TYK_GATEWAY_VERSION#v}_linux_${TYK_GATEWAY_ARCH}.so \
    && cp /plugin-source/post_enrich_*_linux_${TYK_GATEWAY_ARCH}.so /plugin-output/post_enrich.so

FROM scratch AS artifact
ARG TYK_GATEWAY_VERSION
ARG TYK_GATEWAY_ARCH=amd64
LABEL org.opencontainers.image.title="Neutral Tyk Post plugin artifact" \
      io.tyk.gateway.version="${TYK_GATEWAY_VERSION}" \
      io.tyk.gateway.architecture="linux/${TYK_GATEWAY_ARCH}"
COPY --from=compiler /plugin-output/ /
