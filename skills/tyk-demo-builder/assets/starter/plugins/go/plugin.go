package main

import "net/http"

const enrichmentHeader = "X-Demo-Post-Hook"

// PostEnrich runs after Gateway authentication and immediately before the
// request is proxied. Mutating r.Header enriches the request seen upstream.
func PostEnrich(_ http.ResponseWriter, r *http.Request) {
	if r == nil {
		return
	}

	r.Header.Set(enrichmentHeader, "native-go")
}

func main() {}
