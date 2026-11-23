# External Identity Adapters

The local identity service is the default. These neutral adapter values replace
the issuer, audience, JWKS URL and registration endpoint when an external
provider is selected. Keep management credentials in `.env` or a secret store;
never render them into OAS or commit them.

Render one template, verify its discovery/JWKS endpoint from the Gateway
network, then patch the generated JWT OAS. DCR remains blocked until an actual
registration create/read/update/delete cycle passes.
