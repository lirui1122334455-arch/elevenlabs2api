package elevenlabs

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"

	elevenlabsapp "github.com/chenyme/grok2api/backend/internal/application/elevenlabs"
	"github.com/chenyme/grok2api/backend/internal/shared/response"
	"github.com/gin-gonic/gin"
)

type Handler struct{ service *elevenlabsapp.Service }

func NewHandler(service *elevenlabsapp.Service) *Handler { return &Handler{service: service} }

func (h *Handler) Register(router *gin.RouterGroup) {
	router.GET("/elevenlabs/status", h.status)
	router.GET("/elevenlabs/models", h.models)
	router.GET("/elevenlabs/runtime-config", h.runtimeConfig)
	router.PUT("/elevenlabs/runtime-config", h.updateRuntimeConfig)
	router.POST("/elevenlabs/runtime-config/preflight", h.runtimePreflight)
	router.GET("/elevenlabs/registration/status", h.registrationStatus)
	router.GET("/elevenlabs/registration/accounts", h.registrationAccounts)
	router.POST("/elevenlabs/registration/accounts/:id/refresh", h.refreshRegistrationAccount)
	router.POST("/elevenlabs/registration/preflight", h.registrationPreflight)
	router.POST("/elevenlabs/registration/dry-run", h.registrationDryRun)
	router.POST("/elevenlabs/registration/run", h.registrationRun)
	router.POST("/elevenlabs/sound-generation", h.soundGeneration)
	router.POST("/elevenlabs/images/generations", h.imageGeneration)
}

func (h *Handler) runtimeConfig(c *gin.Context) {
	result, err := h.service.RuntimeConfig(c.Request.Context())
	if err != nil {
		h.writeError(c, err)
		return
	}
	response.Success(c, http.StatusOK, result)
}

func (h *Handler) updateRuntimeConfig(c *gin.Context) {
	var input elevenlabsapp.RuntimeConfigUpdate
	if err := c.ShouldBindJSON(&input); err != nil {
		response.Error(c, http.StatusBadRequest, "invalidRequest", "请求参数无效")
		return
	}
	result, err := h.service.UpdateRuntimeConfig(c.Request.Context(), input)
	if err != nil {
		h.writeError(c, err)
		return
	}
	response.Success(c, http.StatusOK, result)
}

func (h *Handler) runtimePreflight(c *gin.Context) {
	result, err := h.service.RuntimePreflight(c.Request.Context())
	if err != nil {
		h.writeError(c, err)
		return
	}
	response.Success(c, http.StatusOK, result)
}

func (h *Handler) registrationStatus(c *gin.Context) {
	response.Success(c, http.StatusOK, h.service.RegistrationStatus(c.Request.Context()))
}

func (h *Handler) registrationAccounts(c *gin.Context) {
	result, err := h.service.RegistrationAccounts(c.Request.Context())
	if err != nil {
		h.writeError(c, err)
		return
	}
	response.Success(c, http.StatusOK, result)
}

func (h *Handler) refreshRegistrationAccount(c *gin.Context) {
	result, err := h.service.RefreshRegistrationAccount(c.Request.Context(), c.Param("id"))
	if err != nil {
		h.writeError(c, err)
		return
	}
	response.Success(c, http.StatusOK, result)
}

func (h *Handler) registrationPreflight(c *gin.Context) { h.registrationAction(c, "preflight") }
func (h *Handler) registrationDryRun(c *gin.Context)    { h.registrationAction(c, "dry-run") }
func (h *Handler) registrationRun(c *gin.Context)       { h.registrationAction(c, "register") }

func (h *Handler) registrationAction(c *gin.Context, action string) {
	count, err := registrationCountFromRequest(c, action)
	if err != nil {
		h.writeError(c, err)
		return
	}
	if strings.Contains(strings.ToLower(c.GetHeader("Accept")), "text/event-stream") {
		h.registrationActionStream(c, action, count)
		return
	}
	result, err := h.service.RegistrationAction(c.Request.Context(), action, count)
	if err != nil {
		h.writeError(c, err)
		return
	}
	response.Success(c, http.StatusOK, result)
}

func (h *Handler) registrationActionStream(c *gin.Context, action string, count int) {
	stream, err := h.service.RegistrationActionStream(c.Request.Context(), action, count)
	if err != nil {
		h.writeError(c, err)
		return
	}
	defer stream.Body.Close()
	c.Header("Content-Type", "text/event-stream; charset=utf-8")
	c.Header("Cache-Control", "no-cache, no-transform")
	c.Header("Connection", "keep-alive")
	c.Header("X-Accel-Buffering", "no")
	c.Status(http.StatusOK)
	c.Writer.Flush()

	buffer := make([]byte, 16<<10)
	for {
		n, readErr := stream.Body.Read(buffer)
		if n > 0 {
			if _, writeErr := c.Writer.Write(buffer[:n]); writeErr != nil {
				return
			}
			c.Writer.Flush()
		}
		if readErr == nil {
			continue
		}
		if readErr != io.EOF && c.Request.Context().Err() == nil {
			payload, _ := json.Marshal(map[string]string{
				"code":    "registration_stream_interrupted",
				"message": "registration log stream was interrupted",
			})
			_, _ = c.Writer.Write([]byte("event: error\ndata: " + string(payload) + "\n\n"))
			c.Writer.Flush()
		}
		return
	}
}

func (h *Handler) status(c *gin.Context) {
	response.Success(c, http.StatusOK, h.service.Status(c.Request.Context()))
}

func (h *Handler) models(c *gin.Context) {
	result, err := h.service.Models(c.Request.Context())
	if err != nil {
		h.writeError(c, err)
		return
	}
	response.Success(c, http.StatusOK, result)
}

func (h *Handler) soundGeneration(c *gin.Context) {
	payload, ok := requestPayload(c)
	if !ok {
		return
	}
	result, err := h.service.GenerateSound(c.Request.Context(), payload)
	if err != nil {
		h.writeError(c, err)
		return
	}
	c.Header("Content-Type", result.ContentType)
	c.Header("Content-Length", strconv.Itoa(len(result.Data)))
	c.Header("Cache-Control", "no-store")
	c.Header("Content-Disposition", `inline; filename="elevenlabs-sound"`)
	if result.CharacterCost != "" {
		c.Header("X-ElevenLabs-Character-Cost", result.CharacterCost)
	}
	if result.RequestID != "" {
		c.Header("X-ElevenLabs-Request-Id", result.RequestID)
	}
	c.Data(http.StatusOK, result.ContentType, result.Data)
}

func (h *Handler) imageGeneration(c *gin.Context) {
	h.forwardJSON(c, "/v1/images/generations")
}

func (h *Handler) forwardJSON(c *gin.Context, path string) {
	payload, ok := requestPayload(c)
	if !ok {
		return
	}
	result, err := h.service.ForwardJSON(c.Request.Context(), path, payload)
	if err != nil {
		h.writeError(c, err)
		return
	}
	response.Success(c, http.StatusOK, result)
}

func registrationCountFromRequest(c *gin.Context, action string) (int, error) {
	if action != "register" {
		_, _ = io.ReadAll(io.LimitReader(c.Request.Body, 1<<20))
		return 1, nil
	}
	var input struct {
		Count int `json:"count"`
	}
	if err := c.ShouldBindJSON(&input); err != nil && !errors.Is(err, io.EOF) {
		return 0, &elevenlabsapp.GatewayError{Status: http.StatusBadRequest, Code: "invalidRequest", Message: "请求参数无效"}
	}
	return elevenlabsapp.NormalizeRegistrationCount(input.Count)
}

func requestPayload(c *gin.Context) (map[string]any, bool) {
	var payload map[string]any
	if err := c.ShouldBindJSON(&payload); err != nil || payload == nil {
		response.Error(c, http.StatusBadRequest, "invalidRequest", "请求参数无效")
		return nil, false
	}
	return payload, true
}

func (h *Handler) writeError(c *gin.Context, err error) {
	var gatewayErr *elevenlabsapp.GatewayError
	if !errors.As(err, &gatewayErr) {
		response.Error(c, http.StatusBadGateway, "elevenLabsGatewayRequestFailed", "ElevenLabs gateway request failed")
		return
	}
	status := gatewayErr.Status
	if gatewayErr.Code == "elevenLabsUnavailable" || gatewayErr.Code == "elevenLabsInvalidConfig" {
		status = http.StatusServiceUnavailable
	} else if status == http.StatusRequestTimeout || status == http.StatusGatewayTimeout {
		status = http.StatusGatewayTimeout
	} else if status < 400 || status >= 500 {
		status = http.StatusBadGateway
	}
	code := strings.TrimSpace(gatewayErr.Code)
	if code == "" {
		code = "elevenLabsGatewayRequestFailed"
	}
	response.Error(c, status, code, gatewayErr.Error())
}
