package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPostEnrich(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "http://upstream.invalid/resource", nil)

	PostEnrich(httptest.NewRecorder(), request)

	if got := request.Header.Get(enrichmentHeader); got != "native-go" {
		t.Fatalf("%s = %q, want native-go", enrichmentHeader, got)
	}
}
