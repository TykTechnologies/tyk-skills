// Package main is a Tyk Gateway Go plugin. Each exported function is one hook.
// Give every new build its own module path in go.mod (demo-v2, demo-v3, ...): a running
// Gateway refuses a second plugin with the same path ("plugin already loaded").
package main

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/TykTechnologies/tyk/ctx"
	"github.com/TykTechnologies/tyk/log"
	"github.com/TykTechnologies/tyk/user"
)

var logger = log.Get()

// AddContext (post): reads the session and the API's config data, then adds headers.
func AddContext(rw http.ResponseWriter, r *http.Request) {
	alias := "none"
	if s := ctx.GetSession(r); s != nil {
		alias = s.Alias
	}
	region := ""
	if data, err := ctx.GetOASConfigData(r); err == nil {
		region, _ = data["region"].(string)
	}
	apiID := ""
	if def := ctx.GetOASDefinition(r); def != nil {
		apiID = def.GetTykExtension().Info.ID
	}
	r.Header.Set("X-Go-Alias", alias)
	r.Header.Set("X-Go-Region", region)
	r.Header.Set("X-Go-API", apiID)
	r.Header.Set("X-Go-Build", "v1")
}

// Deny (pre): answers the client directly when X-Block is present.
func Deny(rw http.ResponseWriter, r *http.Request) {
	if r.Header.Get("X-Block") == "" {
		return
	}
	rw.Header().Set("Content-Type", "application/json")
	rw.WriteHeader(http.StatusForbidden)
	_ = json.NewEncoder(rw).Encode(map[string]string{"code": "PARTNER_BLOCKED"})
}

// PartnerAuth (custom authentication): X-Partner-Token -> a session with its own limits.
func PartnerAuth(rw http.ResponseWriter, r *http.Request) {
	partners := map[string]string{"tok-a-123": "partner-a", "tok-b-456": "partner-b"}
	token := r.Header.Get("X-Partner-Token")
	partner, ok := partners[token]
	if !ok {
		rw.Header().Set("Content-Type", "application/json")
		rw.WriteHeader(http.StatusUnauthorized)
		_ = json.NewEncoder(rw).Encode(map[string]string{"error": "unknown or missing X-Partner-Token"})
		return
	}
	api := ctx.GetOASDefinition(r).GetTykExtension().Info // Tyk OAS API: GetDefinition returns nil
	session := &user.SessionState{
		OrgID: api.OrgID, Alias: partner, Rate: 2, Per: 60, QuotaMax: -1,
		MetaData: map[string]interface{}{"partner": partner, "rate_key": api.ID + ":" + partner,
			"rate_limit_pattern": "$tyk_meta.rate_key"}, // one counter per API and partner
		AccessRights: map[string]user.AccessDefinition{
			api.ID: {APIID: api.ID, APIName: api.Name, Versions: []string{"Default"}},
		},
	}
	ctx.SetSession(r, session, true)
}

// Panic (post): fails on purpose.
func Panic(rw http.ResponseWriter, r *http.Request) {
	panic("plugin bug")
}

// Guarded (post): fails when X-Fail is present, but recovers and answers 500 itself.
func Guarded(rw http.ResponseWriter, r *http.Request) {
	defer func() {
		if v := recover(); v != nil {
			logger.WithField("panic", v).Error("Guarded recovered")
			rw.Header().Set("Content-Type", "application/json")
			rw.WriteHeader(http.StatusInternalServerError)
			_, _ = rw.Write([]byte(`{"error":"plugin failure"}`))
		}
	}()
	if r.Header.Get("X-Fail") != "" {
		var m map[string]string
		m["boom"] = "x" // nil map write: panics
	}
}

// Slow (post): adds 50 ms.
func Slow(rw http.ResponseWriter, r *http.Request) {
	time.Sleep(50 * time.Millisecond)
}

// Noop (post): does nothing; used to measure the cost of the hook itself.
func Noop(rw http.ResponseWriter, r *http.Request) {}

// StampResponse (response): adds a header to the upstream's response.
func StampResponse(rw http.ResponseWriter, res *http.Response, req *http.Request) {
	res.Header.Set("X-Go-Response", "stamped")
	logger.WithField("status", res.StatusCode).Info("StampResponse ran")
}

func main() {}
