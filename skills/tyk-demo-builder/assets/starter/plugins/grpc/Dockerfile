# Tyk Gateway v5.13.1 maps to source commit 8a742352382108a7b905983a14c9deb8c97af4ed.
FROM --platform=$BUILDPLATFORM golang:1.25.1-alpine3.22 AS build
ARG TARGETOS
ARG TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -ldflags="-s -w" -o /out/coprocess-server .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/coprocess-server /coprocess-server
EXPOSE 5555
ENV LISTEN_ADDR=:5555
HEALTHCHECK --interval=10s --timeout=3s --retries=3 CMD ["/coprocess-server", "-healthcheck"]
ENTRYPOINT ["/coprocess-server"]
