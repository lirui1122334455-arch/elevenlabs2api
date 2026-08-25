package elevenlabs

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestServiceStatusModelsAndSound(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer gateway-secret" {
			t.Fatalf("authorization = %q", request.Header.Get("Authorization"))
		}
		switch request.URL.Path {
		case "/healthz":
			_ = json.NewEncoder(writer).Encode(map[string]any{"ok": true, "service": "elevenlabs-gateway", "configured": true, "proxy_configured": true})
		case "/v1/models":
			_ = json.NewEncoder(writer).Encode(map[string]any{"object": "list", "data": []map[string]any{{"id": "gpt-image-2", "object": "model", "created": 1, "owned_by": "elevenlabs-web"}}})
		case "/v1/sound-generation":
			writer.Header().Set("Content-Type", "audio/mpeg")
			writer.Header().Set("X-ElevenLabs-Character-Cost", "42")
			_, _ = writer.Write([]byte("audio"))
		default:
			http.NotFound(writer, request)
		}
	}))
	defer server.Close()

	service := NewService(server.URL, "gateway-secret", "", "")
	status := service.Status(context.Background())
	if !status.Reachable || !status.Configured || !status.ProxyConfigured || status.Error != "" {
		t.Fatalf("status = %#v", status)
	}
	models, err := service.Models(context.Background())
	if err != nil || len(models.Data) != 1 || models.Data[0].ID != "gpt-image-2" || models.Data[0].OwnedBy != "elevenlabs-web" {
		t.Fatalf("models = %#v, err = %v", models, err)
	}
	audio, err := service.GenerateSound(context.Background(), map[string]any{"text": "impact"})
	if err != nil || string(audio.Data) != "audio" || audio.ContentType != "audio/mpeg" || audio.CharacterCost != "42" {
		t.Fatalf("audio = %#v, err = %v", audio, err)
	}
}

func TestServiceForwardsJSONAndPreservesGatewayError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path == "/v1/images/generations" {
			_ = json.NewEncoder(writer).Encode(map[string]any{"created": 1, "data": []map[string]any{{"url": "https://media.example/image.png"}}})
			return
		}
		writer.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(writer).Encode(map[string]any{"error": map[string]any{"code": "hcaptcha_required", "message": "interactive hCaptcha required"}})
	}))
	defer server.Close()

	service := NewService(server.URL, "", "", "")
	result, err := service.ForwardJSON(context.Background(), "/v1/images/generations", map[string]any{"prompt": "product"})
	if err != nil || result["created"].(float64) != 1 {
		t.Fatalf("result = %#v, err = %v", result, err)
	}
	_, err = service.ForwardJSON(context.Background(), "/v1/elevenlabs/image/price", map[string]any{})
	gatewayErr, ok := err.(*GatewayError)
	if !ok || gatewayErr.Status != http.StatusConflict || gatewayErr.Code != "hcaptcha_required" {
		t.Fatalf("error = %#v", err)
	}
}

func TestServiceStatusReportsInvalidConfiguration(t *testing.T) {
	status := NewService("file:///tmp/socket", "", "", "").Status(context.Background())
	if status.Reachable || status.Error == "" {
		t.Fatalf("status = %#v", status)
	}
}

func TestRuntimeConfigSupportsCaptchaGateway(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPut || request.URL.Path != "/v1/runtime-config" {
			http.NotFound(writer, request)
			return
		}
		var payload map[string]any
		if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if payload["captcha_provider"] != "captcha_gateway" || payload["captcha_gateway_api_key"] != "gateway-secret" {
			t.Fatalf("payload = %#v", payload)
		}
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"api_key_configured": false, "api_base_url": "https://api.us.elevenlabs.io",
			"proxy_configured": false, "proxy_label": "direct",
			"request_timeout": 60, "generation_timeout": 240, "registration_timeout": 600,
			"captcha_provider":          "captcha_gateway",
			"yescaptcha_key_configured": true, "yescaptcha_endpoint": "https://api.yescaptcha.com",
			"captcha_gateway_key_configured": true, "captcha_gateway_endpoint": "https://sub.aixiangshu.com",
			"yyds_key_configured": true, "yyds_api_base": "https://maliapi.215.im/v1",
			"mail_domains": "mail.example.com", "revision": 2,
		})
	}))
	defer server.Close()

	service := NewService(server.URL, "", "", "")
	config, err := service.UpdateRuntimeConfig(context.Background(), RuntimeConfigUpdate{
		CaptchaProvider:        "captcha_gateway",
		CaptchaGatewayAPIKey:   "gateway-secret",
		CaptchaGatewayEndpoint: "https://sub.aixiangshu.com",
		YesCaptchaEndpoint:     "https://api.yescaptcha.com",
		APIBaseURL:             "https://api.us.elevenlabs.io",
		RequestTimeout:         60,
		GenerationTimeout:      240,
		RegistrationTimeout:    600,
		YYDSAPIBase:            "https://maliapi.215.im/v1",
	})
	if err != nil || config.CaptchaProvider != "captcha_gateway" || !config.CaptchaGatewayKeyConfigured {
		t.Fatalf("config = %#v, err = %v", config, err)
	}
}

func TestRegistrationActionStreamForwardsEventsAndAuthorization(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/v1/preflight/stream" {
			t.Fatalf("path = %q", request.URL.Path)
		}
		if request.Header.Get("Accept") != "text/event-stream" {
			t.Fatalf("accept = %q", request.Header.Get("Accept"))
		}
		if request.Header.Get("Authorization") != "Bearer register-secret" {
			t.Fatalf("authorization = %q", request.Header.Get("Authorization"))
		}
		writer.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
		_, _ = writer.Write([]byte("event: log\ndata: {\"message\":\"network ready\"}\n\n"))
		_, _ = writer.Write([]byte("event: complete\ndata: {\"ok\":true}\n\n"))
	}))
	defer server.Close()

	service := NewService("", "", server.URL, "register-secret")
	stream, err := service.RegistrationActionStream(context.Background(), "preflight")
	if err != nil {
		t.Fatalf("stream error = %v", err)
	}
	defer stream.Body.Close()
	payload, err := io.ReadAll(stream.Body)
	if err != nil || !strings.Contains(string(payload), "event: log") || !strings.Contains(string(payload), "event: complete") {
		t.Fatalf("payload = %q, err = %v", payload, err)
	}
}

func TestRegistrationActionStreamPreservesUpstreamError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(writer).Encode(map[string]any{"error": map[string]any{"code": "registration_busy", "message": "task is running"}})
	}))
	defer server.Close()

	service := NewService("", "", server.URL, "")
	_, err := service.RegistrationActionStream(context.Background(), "register")
	gatewayErr, ok := err.(*GatewayError)
	if !ok || gatewayErr.Status != http.StatusConflict || gatewayErr.Code != "registration_busy" {
		t.Fatalf("error = %#v", err)
	}
}

func TestRegistrationAccountsAndQuotaRefresh(t *testing.T) {
	const identifier = "0123456789abcdefabcd"
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		account := map[string]any{
			"id": identifier, "email": "one@example.com", "password": "secret",
			"authenticated": true, "status": "active", "tier": "free",
			"quota_used": 120, "quota_limit": 1000, "quota_remaining": 880,
			"quota_reset_at": "2027-01-15T08:00:00+00:00", "quota_updated_at": "2026-08-25T08:00:00+00:00",
			"created_at": "2026-08-25T07:00:00+00:00", "updated_at": "2026-08-25T08:00:00+00:00",
		}
		switch {
		case request.Method == http.MethodGet && request.URL.Path == "/v1/accounts":
			_ = json.NewEncoder(writer).Encode(map[string]any{"ok": true, "accounts": []any{account}})
		case request.Method == http.MethodPost && request.URL.Path == "/v1/accounts/"+identifier+"/refresh":
			_ = json.NewEncoder(writer).Encode(map[string]any{"ok": true, "account": account})
		default:
			http.NotFound(writer, request)
		}
	}))
	defer server.Close()

	service := NewService("", "", server.URL, "")
	accounts, err := service.RegistrationAccounts(context.Background())
	if err != nil || len(accounts) != 1 || accounts[0].QuotaRemaining == nil || *accounts[0].QuotaRemaining != 880 {
		t.Fatalf("accounts = %#v, err = %v", accounts, err)
	}
	account, err := service.RefreshRegistrationAccount(context.Background(), identifier)
	if err != nil || account.Email != "one@example.com" || account.QuotaLimit == nil || *account.QuotaLimit != 1000 {
		t.Fatalf("account = %#v, err = %v", account, err)
	}
	if _, err := service.RefreshRegistrationAccount(context.Background(), "invalid"); err == nil {
		t.Fatal("expected invalid account id error")
	}
}
