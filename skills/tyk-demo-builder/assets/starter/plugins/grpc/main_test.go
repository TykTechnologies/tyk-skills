package main

import (
	"context"
	"testing"

	"github.com/TykTechnologies/tyk/coprocess"
)

func TestPostHookEnrichesRequest(t *testing.T) {
	object := postObject(map[string]string{})

	result, err := (dispatcher{}).Dispatch(context.Background(), object)
	if err != nil {
		t.Fatalf("Dispatch returned an error: %v", err)
	}
	if got := result.Request.SetHeaders[enrichmentHeader]; got != "true" {
		t.Fatalf("enrichment header = %q, want true", got)
	}
	if result.Request.ReturnOverrides != nil {
		t.Fatal("ordinary request unexpectedly received return overrides")
	}
}

func TestPostHookTerminatesBeforeUpstream(t *testing.T) {
	object := postObject(map[string]string{"x-deny-request": "TRUE"})

	result, err := (dispatcher{}).Dispatch(context.Background(), object)
	if err != nil {
		t.Fatalf("Dispatch returned an error: %v", err)
	}
	overrides := result.Request.ReturnOverrides
	if overrides == nil {
		t.Fatal("termination request did not receive return overrides")
	}
	if overrides.ResponseCode != 403 {
		t.Fatalf("response code = %d, want 403", overrides.ResponseCode)
	}
	if !overrides.OverrideError {
		t.Fatal("override_error must preserve the explicit response")
	}
	if overrides.ResponseBody != `{"error":{"code":"PLUGIN_DENIED"}}` {
		t.Fatalf("response body = %q", overrides.ResponseBody)
	}
	if overrides.Headers["Content-Type"] != "application/json" {
		t.Fatal("JSON content type header is missing")
	}
	if _, enriched := result.Request.SetHeaders[enrichmentHeader]; enriched {
		t.Fatal("denied request must not be enriched")
	}
}

func TestPostHookRejectsUnexpectedHook(t *testing.T) {
	object := postObject(nil)
	object.HookType = coprocess.HookType_Pre

	if _, err := (dispatcher{}).Dispatch(context.Background(), object); err == nil {
		t.Fatal("unexpected hook type was accepted")
	}
}

func postObject(headers map[string]string) *coprocess.Object {
	return &coprocess.Object{
		HookType: coprocess.HookType_Post,
		HookName: hookName,
		Request:  &coprocess.MiniRequestObject{Headers: headers},
	}
}
