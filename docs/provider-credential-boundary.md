# Provider credential API boundary

Provider list/create/patch responses no longer include `api_key`, including administrator responses. They provide `has_api_key` and `local_runtime` (null, `onnx`, or `ollama`). `extra_json` is not returned. Existing stored credentials, including local-runtime placeholders, need no migration.

PATCH semantics: omit `api_key` to preserve the credential; send a nonblank value to replace it; send null, an empty string, or whitespace to clear it. Explicit null previously did nothing due to repository partial-update semantics. The dashboard leaves an untouched/blank edit field out of PATCH and uses explicit null only for its clear action. Other omitted fields, including stored extra headers, remain unchanged.

Authorized provider managers may send `provider_id` to draft-test/fetch-models without a new key. The server reuses credentials only when submitted kind and base URL match the stored row, and uses the stored URL and extra headers. Changing the destination requires a new key. Ordinary users cannot call these endpoints. These checks do not grant new provider-management permissions.

Known error classes retain fixed localized guidance. Unknown errors use a generic localized message. Probe errors deliberately do not echo upstream response bodies, URLs, or exception text; those can contain authentication material. Application probe logs similarly omit raw exceptions. This may reduce troubleshooting detail. This change does not add at-rest encryption or claim to sanitize unrelated runtime/library logs.

Deployment note: this fix does not revoke historical disclosures. Operators should rotate any real provider keys that may have been exposed by earlier API responses. Database encryption remains out of scope.
