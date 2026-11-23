package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/TykTechnologies/tyk/coprocess"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/status"
)

const (
	hookName          = "RequestEnrichmentHook"
	enrichmentHeader  = "X-GRPC-Enriched"
	terminationHeader = "X-Deny-Request"
)

type dispatcher struct {
	coprocess.UnimplementedDispatcherServer
}

func (dispatcher) Dispatch(_ context.Context, object *coprocess.Object) (*coprocess.Object, error) {
	if object == nil || object.Request == nil {
		return nil, status.Error(codes.InvalidArgument, "request object is required")
	}
	if object.HookType != coprocess.HookType_Post || object.HookName != hookName {
		return nil, status.Errorf(codes.InvalidArgument, "unsupported hook %q of type %s", object.HookName, object.HookType)
	}

	request := object.Request
	if strings.EqualFold(headerValue(request.Headers, terminationHeader), "true") {
		request.ReturnOverrides = &coprocess.ReturnOverrides{
			ResponseCode: 403,
			ResponseBody: `{"error":{"code":"PLUGIN_DENIED"}}`,
			Headers: map[string]string{
				"Content-Type": "application/json",
			},
			OverrideError: true,
		}
		return object, nil
	}

	if request.SetHeaders == nil {
		request.SetHeaders = make(map[string]string)
	}
	request.SetHeaders[enrichmentHeader] = "true"

	return object, nil
}

func headerValue(headers map[string]string, name string) string {
	for key, value := range headers {
		if strings.EqualFold(key, name) {
			return value
		}
	}
	return ""
}

func main() {
	healthcheck := flag.Bool("healthcheck", false, "check the gRPC health endpoint and exit")
	flag.Parse()

	address := listenAddress()
	if *healthcheck {
		if err := checkHealth(address); err != nil {
			log.Printf("health check failed: %v", err)
			os.Exit(1)
		}
		return
	}

	if err := serve(address); err != nil {
		log.Printf("coprocess server stopped: %v", err)
		os.Exit(1)
	}
}

func serve(address string) error {
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return fmt.Errorf("listen on %s: %w", address, err)
	}

	server := grpc.NewServer()
	coprocess.RegisterDispatcherServer(server, dispatcher{})
	healthServer := health.NewServer()
	healthpb.RegisterHealthServer(server, healthServer)
	healthServer.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-stop
		healthServer.SetServingStatus("", healthpb.HealthCheckResponse_NOT_SERVING)
		server.GracefulStop()
	}()

	log.Printf("neutral Tyk coprocess server listening on %s", address)
	if err := server.Serve(listener); err != nil && !errors.Is(err, grpc.ErrServerStopped) {
		return fmt.Errorf("serve gRPC: %w", err)
	}
	return nil
}

func checkHealth(address string) error {
	target := address
	if strings.HasPrefix(target, ":") {
		target = "127.0.0.1" + target
	}

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	connection, err := grpc.DialContext(ctx, target, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithBlock())
	if err != nil {
		return err
	}
	defer connection.Close()

	response, err := healthpb.NewHealthClient(connection).Check(ctx, &healthpb.HealthCheckRequest{})
	if err != nil {
		return err
	}
	if response.Status != healthpb.HealthCheckResponse_SERVING {
		return fmt.Errorf("unexpected serving status: %s", response.Status)
	}
	return nil
}

func envOrDefault(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func listenAddress() string {
	if value := strings.TrimSpace(os.Getenv("LISTEN_ADDR")); value != "" {
		return value
	}
	return envOrDefault("LISTEN_ADDRESS", ":5555")
}
