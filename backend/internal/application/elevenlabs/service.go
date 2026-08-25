package elevenlabs

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	defaultGatewayURL  = "http://127.0.0.1:8092"
	defaultRegisterURL = "http://127.0.0.1:8093"
	maxJSONBytes       = int64(2 << 20)
	maxAudioBytes      = int64(32 << 20)
)

type Status struct {
	Reachable       bool   `json:"reachable"`
	Configured      bool   `json:"configured"`
	AccountPoolSize int    `json:"accountPoolSize"`
	ProxyConfigured bool   `json:"proxyConfigured"`
	Service         string `json:"service"`
	Error           string `json:"error,omitempty"`
}

type Model struct {
	ID      string `json:"id"`
	Object  string `json:"object"`
	Created int64  `json:"created"`
	OwnedBy string `json:"ownedBy"`
}

type ModelList struct {
	Object string  `json:"object"`
	Data   []Model `json:"data"`
}

type AudioResult struct {
	Data          []byte
	ContentType   string
	CharacterCost string
	RequestID     string
}

type RuntimeConfig struct {
	APIKeyConfigured            bool    `json:"apiKeyConfigured"`
	APIBaseURL                  string  `json:"apiBaseURL"`
	ProxyConfigured             bool    `json:"proxyConfigured"`
	ProxyLabel                  string  `json:"proxyLabel"`
	RequestTimeout              float64 `json:"requestTimeout"`
	GenerationTimeout           float64 `json:"generationTimeout"`
	RegistrationTimeout         float64 `json:"registrationTimeout"`
	CaptchaProvider             string  `json:"captchaProvider"`
	YesCaptchaKeyConfigured     bool    `json:"yesCaptchaKeyConfigured"`
	YesCaptchaEndpoint          string  `json:"yesCaptchaEndpoint"`
	CaptchaGatewayKeyConfigured bool    `json:"captchaGatewayKeyConfigured"`
	CaptchaGatewayEndpoint      string  `json:"captchaGatewayEndpoint"`
	YYDSKeyConfigured           bool    `json:"yydsKeyConfigured"`
	YYDSAPIBase                 string  `json:"yydsAPIBase"`
	MailDomains                 string  `json:"mailDomains"`
	Revision                    int64   `json:"revision"`
}

type RuntimeConfigUpdate struct {
	APIKey                    string  `json:"apiKey"`
	ClearAPIKey               bool    `json:"clearAPIKey"`
	APIBaseURL                string  `json:"apiBaseURL"`
	ProxyURL                  string  `json:"proxyURL"`
	RequestTimeout            float64 `json:"requestTimeout"`
	GenerationTimeout         float64 `json:"generationTimeout"`
	RegistrationTimeout       float64 `json:"registrationTimeout"`
	CaptchaProvider           string  `json:"captchaProvider"`
	YesCaptchaAPIKey          string  `json:"yesCaptchaAPIKey"`
	ClearYesCaptchaAPIKey     bool    `json:"clearYesCaptchaAPIKey"`
	YesCaptchaEndpoint        string  `json:"yesCaptchaEndpoint"`
	CaptchaGatewayAPIKey      string  `json:"captchaGatewayAPIKey"`
	ClearCaptchaGatewayAPIKey bool    `json:"clearCaptchaGatewayAPIKey"`
	CaptchaGatewayEndpoint    string  `json:"captchaGatewayEndpoint"`
	YYDSAPIKey                string  `json:"yydsAPIKey"`
	ClearYYDSAPIKey           bool    `json:"clearYYDSAPIKey"`
	YYDSAPIBase               string  `json:"yydsAPIBase"`
	MailDomains               string  `json:"mailDomains"`
}

type RegistrationStatus struct {
	Reachable         bool   `json:"reachable"`
	Running           bool   `json:"running"`
	Connection        string `json:"connection"`
	CaptchaProvider   string `json:"captchaProvider"`
	CaptchaConfigured bool   `json:"captchaConfigured"`
	MailConfigured    bool   `json:"mailConfigured"`
	RuntimeRevision   int64  `json:"runtimeRevision"`
	Error             string `json:"error,omitempty"`
}

type RegistrationResult struct {
	OK            bool     `json:"ok"`
	Email         string   `json:"email,omitempty"`
	Password      string   `json:"password,omitempty"`
	Authenticated bool     `json:"authenticated,omitempty"`
	FinalURL      string   `json:"finalURL,omitempty"`
	Connection    string   `json:"connection,omitempty"`
	Status        int      `json:"status,omitempty"`
	Logs          []string `json:"logs,omitempty"`
}

type RegistrationAccount struct {
	ID               string `json:"id"`
	Email            string `json:"email"`
	Password         string `json:"password"`
	Authenticated    bool   `json:"authenticated"`
	APIKeyConfigured bool   `json:"apiKeyConfigured"`
	Status           string `json:"status"`
	Tier             string `json:"tier"`
	QuotaUsed        *int64 `json:"quotaUsed"`
	QuotaLimit       *int64 `json:"quotaLimit"`
	QuotaRemaining   *int64 `json:"quotaRemaining"`
	QuotaResetAt     string `json:"quotaResetAt"`
	QuotaUpdatedAt   string `json:"quotaUpdatedAt"`
	CreatedAt        string `json:"createdAt"`
	UpdatedAt        string `json:"updatedAt"`
}

type registrationAccountWire struct {
	ID               string `json:"id"`
	Email            string `json:"email"`
	Password         string `json:"password"`
	Authenticated    bool   `json:"authenticated"`
	APIKeyConfigured bool   `json:"api_key_configured"`
	Status           string `json:"status"`
	Tier             string `json:"tier"`
	QuotaUsed        *int64 `json:"quota_used"`
	QuotaLimit       *int64 `json:"quota_limit"`
	QuotaRemaining   *int64 `json:"quota_remaining"`
	QuotaResetAt     string `json:"quota_reset_at"`
	QuotaUpdatedAt   string `json:"quota_updated_at"`
	CreatedAt        string `json:"created_at"`
	UpdatedAt        string `json:"updated_at"`
}

type RegistrationEventStream struct {
	Body io.ReadCloser
}

var registrationAccountIDPattern = regexp.MustCompile(`^[0-9a-f]{20}$`)

type GatewayError struct {
	Status  int
	Code    string
	Message string
}

func (e *GatewayError) Error() string {
	if strings.TrimSpace(e.Message) != "" {
		return e.Message
	}
	return http.StatusText(e.Status)
}

type Service struct {
	baseURL           string
	gatewayKey        string
	client            *http.Client
	configErr         error
	registerURL       string
	registerKey       string
	registerClient    *http.Client
	registerConfigErr error
}

func NewService(rawBaseURL, gatewayKey, rawRegisterURL, registerKey string) *Service {
	baseURL, err := normalizeServiceURL(rawBaseURL, defaultGatewayURL, "ELEVENLABS_GATEWAY_URL")
	registerURL, registerErr := normalizeServiceURL(rawRegisterURL, defaultRegisterURL, "ELEVENLABS_REGISTER_URL")
	transport := &http.Transport{
		Proxy: nil,
		DialContext: (&net.Dialer{
			Timeout:   10 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          16,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: 4 * time.Minute,
	}
	registerTransport := transport.Clone()
	return &Service{
		baseURL: baseURL, gatewayKey: strings.TrimSpace(gatewayKey), configErr: err,
		client:      &http.Client{Transport: transport, Timeout: 5 * time.Minute},
		registerURL: registerURL, registerKey: strings.TrimSpace(registerKey), registerConfigErr: registerErr,
		registerClient: &http.Client{Transport: registerTransport, Timeout: 31 * time.Minute},
	}
}

func (s *Service) Status(ctx context.Context) Status {
	result := Status{Service: "elevenlabs-gateway"}
	if s == nil {
		result.Error = "ElevenLabs gateway service is unavailable"
		return result
	}
	requestCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	body, _, err := s.do(requestCtx, http.MethodGet, "/healthz", nil, maxJSONBytes)
	if err != nil {
		result.Error = err.Error()
		return result
	}
	var payload struct {
		OK              bool   `json:"ok"`
		Service         string `json:"service"`
		Configured      bool   `json:"configured"`
		AccountPoolSize int    `json:"account_pool_size"`
		ProxyConfigured bool   `json:"proxy_configured"`
	}
	if err := json.Unmarshal(body, &payload); err != nil || !payload.OK {
		result.Error = "ElevenLabs gateway returned an invalid health response"
		return result
	}
	result.Reachable = true
	result.Configured = payload.Configured
	result.AccountPoolSize = payload.AccountPoolSize
	result.ProxyConfigured = payload.ProxyConfigured
	if strings.TrimSpace(payload.Service) != "" {
		result.Service = payload.Service
	}
	return result
}

func (s *Service) Models(ctx context.Context) (ModelList, error) {
	body, _, err := s.do(ctx, http.MethodGet, "/v1/models", nil, maxJSONBytes)
	if err != nil {
		return ModelList{}, err
	}
	var payload struct {
		Object string `json:"object"`
		Data   []struct {
			ID      string `json:"id"`
			Object  string `json:"object"`
			Created int64  `json:"created"`
			OwnedBy string `json:"owned_by"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return ModelList{}, invalidResponseError()
	}
	models := make([]Model, 0, len(payload.Data))
	for _, value := range payload.Data {
		if strings.TrimSpace(value.ID) == "" {
			return ModelList{}, invalidResponseError()
		}
		models = append(models, Model{ID: value.ID, Object: value.Object, Created: value.Created, OwnedBy: value.OwnedBy})
	}
	return ModelList{Object: payload.Object, Data: models}, nil
}

func (s *Service) ForwardJSON(ctx context.Context, path string, payload map[string]any) (map[string]any, error) {
	requestBody, err := json.Marshal(payload)
	if err != nil {
		return nil, &GatewayError{Status: http.StatusBadRequest, Code: "invalidRequest", Message: "request body is invalid"}
	}
	body, _, err := s.do(ctx, http.MethodPost, path, requestBody, maxJSONBytes)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := json.Unmarshal(body, &result); err != nil || result == nil {
		return nil, invalidResponseError()
	}
	return result, nil
}

func (s *Service) GenerateSound(ctx context.Context, payload map[string]any) (AudioResult, error) {
	requestBody, err := json.Marshal(payload)
	if err != nil {
		return AudioResult{}, &GatewayError{Status: http.StatusBadRequest, Code: "invalidRequest", Message: "request body is invalid"}
	}
	body, headers, err := s.do(ctx, http.MethodPost, "/v1/sound-generation", requestBody, maxAudioBytes)
	if err != nil {
		return AudioResult{}, err
	}
	contentType := strings.TrimSpace(strings.Split(headers.Get("Content-Type"), ";")[0])
	if !strings.HasPrefix(contentType, "audio/") || len(body) == 0 {
		return AudioResult{}, invalidResponseError()
	}
	return AudioResult{
		Data: body, ContentType: contentType,
		CharacterCost: headers.Get("X-ElevenLabs-Character-Cost"),
		RequestID:     firstNonEmpty(headers.Get("X-ElevenLabs-Request-Id"), headers.Get("X-ElevenLabs-X-Trace-Id")),
	}, nil
}

func (s *Service) RuntimeConfig(ctx context.Context) (RuntimeConfig, error) {
	body, _, err := s.do(ctx, http.MethodGet, "/v1/runtime-config", nil, maxJSONBytes)
	if err != nil {
		return RuntimeConfig{}, err
	}
	return decodeRuntimeConfig(body)
}

func (s *Service) UpdateRuntimeConfig(ctx context.Context, input RuntimeConfigUpdate) (RuntimeConfig, error) {
	payload := map[string]any{
		"api_key": input.APIKey, "clear_api_key": input.ClearAPIKey,
		"api_base_url": input.APIBaseURL, "proxy_url": input.ProxyURL,
		"request_timeout": input.RequestTimeout, "generation_timeout": input.GenerationTimeout,
		"registration_timeout":          input.RegistrationTimeout,
		"captcha_provider":              input.CaptchaProvider,
		"yescaptcha_api_key":            input.YesCaptchaAPIKey,
		"clear_yescaptcha_api_key":      input.ClearYesCaptchaAPIKey,
		"yescaptcha_endpoint":           input.YesCaptchaEndpoint,
		"captcha_gateway_api_key":       input.CaptchaGatewayAPIKey,
		"clear_captcha_gateway_api_key": input.ClearCaptchaGatewayAPIKey,
		"captcha_gateway_endpoint":      input.CaptchaGatewayEndpoint,
		"yyds_api_key":                  input.YYDSAPIKey, "clear_yyds_api_key": input.ClearYYDSAPIKey,
		"yyds_api_base": input.YYDSAPIBase, "mail_domains": input.MailDomains,
	}
	requestBody, err := json.Marshal(payload)
	if err != nil {
		return RuntimeConfig{}, &GatewayError{Status: http.StatusBadRequest, Code: "invalidRequest", Message: "runtime configuration is invalid"}
	}
	body, _, err := s.do(ctx, http.MethodPut, "/v1/runtime-config", requestBody, maxJSONBytes)
	if err != nil {
		return RuntimeConfig{}, err
	}
	return decodeRuntimeConfig(body)
}

func (s *Service) RuntimePreflight(ctx context.Context) (map[string]any, error) {
	body, _, err := s.do(ctx, http.MethodPost, "/v1/runtime-config/preflight", []byte("{}"), maxJSONBytes)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	if err := json.Unmarshal(body, &result); err != nil || result == nil {
		return nil, invalidResponseError()
	}
	return result, nil
}

func (s *Service) RegistrationStatus(ctx context.Context) RegistrationStatus {
	result := RegistrationStatus{}
	requestCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	body, _, err := s.doRegister(requestCtx, http.MethodGet, "/healthz", nil, maxJSONBytes)
	if err != nil {
		result.Error = err.Error()
		return result
	}
	var payload struct {
		OK                bool   `json:"ok"`
		Running           bool   `json:"running"`
		Connection        string `json:"connection"`
		CaptchaProvider   string `json:"captcha_provider"`
		CaptchaConfigured bool   `json:"captcha_configured"`
		MailConfigured    bool   `json:"mail_configured"`
		RuntimeRevision   int64  `json:"runtime_revision"`
		Error             string `json:"error"`
	}
	if err := json.Unmarshal(body, &payload); err != nil || !payload.OK {
		result.Error = "ElevenLabs registration service returned an invalid health response"
		return result
	}
	result.Reachable = true
	result.Running = payload.Running
	result.Connection = payload.Connection
	result.CaptchaProvider = payload.CaptchaProvider
	result.CaptchaConfigured = payload.CaptchaConfigured
	result.MailConfigured = payload.MailConfigured
	result.RuntimeRevision = payload.RuntimeRevision
	result.Error = payload.Error
	return result
}

func (s *Service) RegistrationAction(ctx context.Context, action string) (RegistrationResult, error) {
	path, err := registrationActionPath(action, false)
	if err != nil {
		return RegistrationResult{}, err
	}
	body, _, err := s.doRegister(ctx, http.MethodPost, path, []byte("{}"), maxJSONBytes)
	if err != nil {
		return RegistrationResult{}, err
	}
	var payload struct {
		OK            bool     `json:"ok"`
		Email         string   `json:"email"`
		Password      string   `json:"password"`
		Authenticated bool     `json:"authenticated"`
		FinalURL      string   `json:"final_url"`
		Connection    string   `json:"connection"`
		Status        int      `json:"status"`
		Logs          []string `json:"logs"`
	}
	if err := json.Unmarshal(body, &payload); err != nil || !payload.OK {
		return RegistrationResult{}, invalidResponseError()
	}
	return RegistrationResult{OK: true, Email: payload.Email, Password: payload.Password, Authenticated: payload.Authenticated, FinalURL: payload.FinalURL, Connection: payload.Connection, Status: payload.Status, Logs: payload.Logs}, nil
}

func (s *Service) RegistrationAccounts(ctx context.Context) ([]RegistrationAccount, error) {
	body, _, err := s.doRegister(ctx, http.MethodGet, "/v1/accounts", nil, maxJSONBytes)
	if err != nil {
		return nil, err
	}
	var payload struct {
		OK       bool                      `json:"ok"`
		Accounts []registrationAccountWire `json:"accounts"`
	}
	if err := json.Unmarshal(body, &payload); err != nil || !payload.OK {
		return nil, invalidResponseError()
	}
	accounts := make([]RegistrationAccount, 0, len(payload.Accounts))
	for _, account := range payload.Accounts {
		if !registrationAccountIDPattern.MatchString(account.ID) || strings.TrimSpace(account.Email) == "" {
			return nil, invalidResponseError()
		}
		accounts = append(accounts, registrationAccountFromWire(account))
	}
	return accounts, nil
}

func (s *Service) RefreshRegistrationAccount(ctx context.Context, identifier string) (RegistrationAccount, error) {
	if !registrationAccountIDPattern.MatchString(identifier) {
		return RegistrationAccount{}, &GatewayError{Status: http.StatusBadRequest, Code: "invalidRequest", Message: "invalid ElevenLabs account id"}
	}
	body, _, err := s.doRegister(
		ctx,
		http.MethodPost,
		"/v1/accounts/"+identifier+"/refresh",
		[]byte("{}"),
		maxJSONBytes,
	)
	if err != nil {
		return RegistrationAccount{}, err
	}
	var payload struct {
		OK      bool                    `json:"ok"`
		Account registrationAccountWire `json:"account"`
	}
	if err := json.Unmarshal(body, &payload); err != nil || !payload.OK || payload.Account.ID != identifier {
		return RegistrationAccount{}, invalidResponseError()
	}
	return registrationAccountFromWire(payload.Account), nil
}

func registrationAccountFromWire(account registrationAccountWire) RegistrationAccount {
	return RegistrationAccount{
		ID: account.ID, Email: account.Email, Password: account.Password,
		Authenticated: account.Authenticated, APIKeyConfigured: account.APIKeyConfigured, Status: account.Status, Tier: account.Tier,
		QuotaUsed: account.QuotaUsed, QuotaLimit: account.QuotaLimit, QuotaRemaining: account.QuotaRemaining,
		QuotaResetAt: account.QuotaResetAt, QuotaUpdatedAt: account.QuotaUpdatedAt,
		CreatedAt: account.CreatedAt, UpdatedAt: account.UpdatedAt,
	}
}

func (s *Service) RegistrationActionStream(ctx context.Context, action string) (RegistrationEventStream, error) {
	if s == nil {
		return RegistrationEventStream{}, unavailableError("ElevenLabs registration service is unavailable")
	}
	if s.registerConfigErr != nil {
		return RegistrationEventStream{}, &GatewayError{Status: http.StatusServiceUnavailable, Code: "elevenLabsInvalidConfig", Message: s.registerConfigErr.Error()}
	}
	path, err := registrationActionPath(action, true)
	if err != nil {
		return RegistrationEventStream{}, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, s.registerURL+path, bytes.NewReader([]byte("{}")))
	if err != nil {
		return RegistrationEventStream{}, unavailableError("failed to create ElevenLabs registration request")
	}
	request.Header.Set("Accept", "text/event-stream")
	request.Header.Set("Content-Type", "application/json")
	if s.registerKey != "" {
		request.Header.Set("Authorization", "Bearer "+s.registerKey)
	}
	response, err := s.registerClient.Do(request)
	if err != nil {
		return RegistrationEventStream{}, unavailableError("ElevenLabs registration service is unreachable")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		defer response.Body.Close()
		data, readErr := readLimited(response.Body, maxJSONBytes)
		if readErr != nil {
			return RegistrationEventStream{}, readErr
		}
		return RegistrationEventStream{}, gatewayResponseError(response.StatusCode, data)
	}
	contentType := strings.ToLower(strings.TrimSpace(response.Header.Get("Content-Type")))
	if !strings.HasPrefix(contentType, "text/event-stream") {
		response.Body.Close()
		return RegistrationEventStream{}, invalidResponseError()
	}
	return RegistrationEventStream{Body: response.Body}, nil
}

func registrationActionPath(action string, stream bool) (string, error) {
	paths := map[string]string{"preflight": "/v1/preflight", "dry-run": "/v1/dry-run", "register": "/v1/register"}
	path, ok := paths[action]
	if !ok {
		return "", &GatewayError{Status: http.StatusBadRequest, Code: "invalidRequest", Message: "invalid registration action"}
	}
	if stream {
		path += "/stream"
	}
	return path, nil
}

func (s *Service) do(ctx context.Context, method, path string, payload []byte, limit int64) ([]byte, http.Header, error) {
	if s == nil {
		return nil, nil, unavailableError("ElevenLabs gateway service is unavailable")
	}
	if s.configErr != nil {
		return nil, nil, &GatewayError{Status: http.StatusServiceUnavailable, Code: "elevenLabsInvalidConfig", Message: s.configErr.Error()}
	}
	var body io.Reader
	if payload != nil {
		body = bytes.NewReader(payload)
	}
	request, err := http.NewRequestWithContext(ctx, method, s.baseURL+path, body)
	if err != nil {
		return nil, nil, unavailableError("failed to create ElevenLabs gateway request")
	}
	request.Header.Set("Accept", "application/json, audio/*")
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	if s.gatewayKey != "" {
		request.Header.Set("Authorization", "Bearer "+s.gatewayKey)
	}
	response, err := s.client.Do(request)
	if err != nil {
		return nil, nil, unavailableError("ElevenLabs gateway is unreachable")
	}
	defer response.Body.Close()
	data, err := readLimited(response.Body, limit)
	if err != nil {
		return nil, nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, nil, gatewayResponseError(response.StatusCode, data)
	}
	return data, response.Header.Clone(), nil
}

func (s *Service) doRegister(ctx context.Context, method, path string, payload []byte, limit int64) ([]byte, http.Header, error) {
	if s == nil {
		return nil, nil, unavailableError("ElevenLabs registration service is unavailable")
	}
	if s.registerConfigErr != nil {
		return nil, nil, &GatewayError{Status: http.StatusServiceUnavailable, Code: "elevenLabsInvalidConfig", Message: s.registerConfigErr.Error()}
	}
	return doServiceRequest(ctx, s.registerClient, s.registerURL, s.registerKey, method, path, payload, limit)
}

func doServiceRequest(ctx context.Context, client *http.Client, baseURL, key, method, path string, payload []byte, limit int64) ([]byte, http.Header, error) {
	var body io.Reader
	if payload != nil {
		body = bytes.NewReader(payload)
	}
	request, err := http.NewRequestWithContext(ctx, method, baseURL+path, body)
	if err != nil {
		return nil, nil, unavailableError("failed to create ElevenLabs service request")
	}
	request.Header.Set("Accept", "application/json")
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	if key != "" {
		request.Header.Set("Authorization", "Bearer "+key)
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, nil, unavailableError("ElevenLabs service is unreachable")
	}
	defer response.Body.Close()
	data, err := readLimited(response.Body, limit)
	if err != nil {
		return nil, nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, nil, gatewayResponseError(response.StatusCode, data)
	}
	return data, response.Header.Clone(), nil
}

func normalizeServiceURL(value, defaultValue, name string) (string, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		value = defaultValue
	}
	parsed, err := url.Parse(value)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", fmt.Errorf("%s is invalid", name)
	}
	return strings.TrimRight(parsed.String(), "/"), nil
}

func decodeRuntimeConfig(body []byte) (RuntimeConfig, error) {
	var payload struct {
		APIKeyConfigured            bool    `json:"api_key_configured"`
		APIBaseURL                  string  `json:"api_base_url"`
		ProxyConfigured             bool    `json:"proxy_configured"`
		ProxyLabel                  string  `json:"proxy_label"`
		RequestTimeout              float64 `json:"request_timeout"`
		GenerationTimeout           float64 `json:"generation_timeout"`
		RegistrationTimeout         float64 `json:"registration_timeout"`
		CaptchaProvider             string  `json:"captcha_provider"`
		YesCaptchaKeyConfigured     bool    `json:"yescaptcha_key_configured"`
		YesCaptchaEndpoint          string  `json:"yescaptcha_endpoint"`
		CaptchaGatewayKeyConfigured bool    `json:"captcha_gateway_key_configured"`
		CaptchaGatewayEndpoint      string  `json:"captcha_gateway_endpoint"`
		YYDSKeyConfigured           bool    `json:"yyds_key_configured"`
		YYDSAPIBase                 string  `json:"yyds_api_base"`
		MailDomains                 string  `json:"mail_domains"`
		Revision                    int64   `json:"revision"`
	}
	if err := json.Unmarshal(body, &payload); err != nil || payload.APIBaseURL == "" {
		return RuntimeConfig{}, invalidResponseError()
	}
	return RuntimeConfig{
		APIKeyConfigured: payload.APIKeyConfigured, APIBaseURL: payload.APIBaseURL,
		ProxyConfigured: payload.ProxyConfigured, ProxyLabel: payload.ProxyLabel,
		RequestTimeout: payload.RequestTimeout, GenerationTimeout: payload.GenerationTimeout,
		RegistrationTimeout:     payload.RegistrationTimeout,
		CaptchaProvider:         payload.CaptchaProvider,
		YesCaptchaKeyConfigured: payload.YesCaptchaKeyConfigured, YesCaptchaEndpoint: payload.YesCaptchaEndpoint,
		CaptchaGatewayKeyConfigured: payload.CaptchaGatewayKeyConfigured, CaptchaGatewayEndpoint: payload.CaptchaGatewayEndpoint,
		YYDSKeyConfigured: payload.YYDSKeyConfigured, YYDSAPIBase: payload.YYDSAPIBase,
		MailDomains: payload.MailDomains, Revision: payload.Revision,
	}, nil
}

func readLimited(reader io.Reader, limit int64) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(reader, limit+1))
	if err != nil {
		return nil, unavailableError("failed to read ElevenLabs gateway response")
	}
	if int64(len(data)) > limit {
		return nil, &GatewayError{Status: http.StatusBadGateway, Code: "elevenLabsResponseTooLarge", Message: "ElevenLabs gateway response is too large"}
	}
	return data, nil
}

func gatewayResponseError(status int, data []byte) error {
	var envelope struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	_ = json.Unmarshal(data, &envelope)
	code := strings.TrimSpace(envelope.Error.Code)
	if code == "" {
		code = "elevenLabsGatewayRequestFailed"
	}
	message := strings.TrimSpace(envelope.Error.Message)
	if message == "" {
		message = "ElevenLabs gateway returned HTTP " + strconv.Itoa(status)
	}
	return &GatewayError{Status: status, Code: code, Message: message}
}

func unavailableError(message string) error {
	return &GatewayError{Status: http.StatusServiceUnavailable, Code: "elevenLabsUnavailable", Message: message}
}

func invalidResponseError() error {
	return &GatewayError{Status: http.StatusBadGateway, Code: "elevenLabsInvalidResponse", Message: "ElevenLabs gateway returned an invalid response"}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}
