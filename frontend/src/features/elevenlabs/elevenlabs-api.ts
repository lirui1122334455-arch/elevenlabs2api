import { ApiError, apiBlobRequest, apiEventStream, apiRequest } from "@/shared/api/client";
import { createObjectDecoder, createValidatedDecoder, hasShape, isArrayOf, isBoolean, isNumber, isObject, isOptional, isString } from "@/shared/api/decoder";

export type ElevenLabsStatusDTO = {
  reachable: boolean;
  configured: boolean;
  proxyConfigured: boolean;
  service: string;
  error?: string;
};

export type ElevenLabsModelDTO = {
  id: string;
  object: string;
  created: number;
  ownedBy: string;
};

export type ElevenLabsModelListDTO = {
  object: string;
  data: ElevenLabsModelDTO[];
};

export type ElevenLabsCaptchaProvider = "yescaptcha" | "captcha_gateway";

const isCaptchaProvider = (value: unknown): value is ElevenLabsCaptchaProvider => value === "yescaptcha" || value === "captcha_gateway";

export type ElevenLabsRuntimeConfigDTO = {
  apiKeyConfigured: boolean;
  apiBaseURL: string;
  proxyConfigured: boolean;
  proxyLabel: string;
  requestTimeout: number;
  generationTimeout: number;
  registrationTimeout: number;
  captchaProvider: ElevenLabsCaptchaProvider;
  yesCaptchaKeyConfigured: boolean;
  yesCaptchaEndpoint: string;
  captchaGatewayKeyConfigured: boolean;
  captchaGatewayEndpoint: string;
  yydsKeyConfigured: boolean;
  yydsAPIBase: string;
  mailDomains: string;
  revision: number;
};

export type ElevenLabsRuntimeConfigInput = {
  apiKey: string;
  clearAPIKey: boolean;
  apiBaseURL: string;
  proxyURL: string;
  requestTimeout: number;
  generationTimeout: number;
  registrationTimeout: number;
  captchaProvider: ElevenLabsCaptchaProvider;
  yesCaptchaAPIKey: string;
  clearYesCaptchaAPIKey: boolean;
  yesCaptchaEndpoint: string;
  captchaGatewayAPIKey: string;
  clearCaptchaGatewayAPIKey: boolean;
  captchaGatewayEndpoint: string;
  yydsAPIKey: string;
  clearYYDSAPIKey: boolean;
  yydsAPIBase: string;
  mailDomains: string;
};

export type ElevenLabsRuntimePreflightDTO = {
  ready: boolean;
  connection: Record<string, unknown>;
  api_key: Record<string, unknown>;
  captcha: Record<string, unknown>;
  yescaptcha: Record<string, unknown>;
  yyds: Record<string, unknown>;
};

export type ElevenLabsRegistrationStatusDTO = {
  reachable: boolean;
  running: boolean;
  connection: string;
  captchaProvider: ElevenLabsCaptchaProvider;
  captchaConfigured: boolean;
  mailConfigured: boolean;
  runtimeRevision: number;
  error?: string;
};

export type ElevenLabsRegistrationResultDTO = {
  ok: boolean;
  email?: string;
  password?: string;
  authenticated?: boolean;
  finalURL?: string;
  connection?: string;
  status?: number;
  logs?: string[];
};

export type ElevenLabsRegistrationAccountDTO = {
  id: string;
  email: string;
  password: string;
  authenticated: boolean;
  apiKeyConfigured: boolean;
  status: string;
  tier: string;
  quotaUsed: number | null;
  quotaLimit: number | null;
  quotaRemaining: number | null;
  quotaResetAt: string;
  quotaUpdatedAt: string;
  createdAt: string;
  updatedAt: string;
};

type ElevenLabsRegistrationStreamEventDTO = {
  message?: string;
  code?: string;
  ok?: boolean;
  email?: string;
  password?: string;
  authenticated?: boolean;
  final_url?: string;
  connection?: string;
  status?: number;
};

export type SoundGenerationInput = {
  text: string;
  model: string;
  duration_seconds: number;
  prompt_influence: number;
  loop: boolean;
  output_format: string;
};

export type ImageGenerationInput = {
  model: "gpt-image-2";
  prompt: string;
  aspect_ratio: string;
  resolution: "1K" | "2K" | "4K";
  quality: "low" | "medium" | "high";
  images?: Array<{
    type: "inline_base64";
    content_base64: string;
    mime_type: "image/png" | "image/jpeg" | "image/webp";
  }>;
  response_format: "url";
};

export type ImageGenerationDTO = {
  created: number;
  data: Array<{ url?: string; b64_json?: string; revised_prompt?: string }>;
};

const statusDecoder = createObjectDecoder<ElevenLabsStatusDTO>("ElevenLabs status", {
  reachable: isBoolean,
  configured: isBoolean,
  proxyConfigured: isBoolean,
  service: isString,
  error: isOptional(isString),
});

const modelValidator = hasShape({ id: isString, object: isString, created: isNumber, ownedBy: isString });
const modelListDecoder = createObjectDecoder<ElevenLabsModelListDTO>("ElevenLabs models", {
  object: isString,
  data: isArrayOf(modelValidator),
});

const imageItemValidator = hasShape({
  url: isOptional(isString),
  b64_json: isOptional(isString),
  revised_prompt: isOptional(isString),
});
const imageGenerationDecoder = createObjectDecoder<ImageGenerationDTO>("ElevenLabs image generation", {
  created: isNumber,
  data: isArrayOf(imageItemValidator),
});

const runtimeConfigDecoder = createObjectDecoder<ElevenLabsRuntimeConfigDTO>("ElevenLabs runtime config", {
  apiKeyConfigured: isBoolean,
  apiBaseURL: isString,
  proxyConfigured: isBoolean,
  proxyLabel: isString,
  requestTimeout: isNumber,
  generationTimeout: isNumber,
  registrationTimeout: isNumber,
  captchaProvider: isCaptchaProvider,
  yesCaptchaKeyConfigured: isBoolean,
  yesCaptchaEndpoint: isString,
  captchaGatewayKeyConfigured: isBoolean,
  captchaGatewayEndpoint: isString,
  yydsKeyConfigured: isBoolean,
  yydsAPIBase: isString,
  mailDomains: isString,
  revision: isNumber,
});

const runtimePreflightDecoder = createObjectDecoder<ElevenLabsRuntimePreflightDTO>("ElevenLabs runtime preflight", {
  ready: isBoolean,
  connection: isObject,
  api_key: isObject,
  captcha: isObject,
  yescaptcha: isObject,
  yyds: isObject,
});

const registrationStatusDecoder = createObjectDecoder<ElevenLabsRegistrationStatusDTO>("ElevenLabs registration status", {
  reachable: isBoolean,
  running: isBoolean,
  connection: isString,
  captchaProvider: isCaptchaProvider,
  captchaConfigured: isBoolean,
  mailConfigured: isBoolean,
  runtimeRevision: isNumber,
  error: isOptional(isString),
});

const registrationResultDecoder = createObjectDecoder<ElevenLabsRegistrationResultDTO>("ElevenLabs registration result", {
  ok: isBoolean,
  email: isOptional(isString),
  password: isOptional(isString),
  authenticated: isOptional(isBoolean),
  finalURL: isOptional(isString),
  connection: isOptional(isString),
  status: isOptional(isNumber),
  logs: isOptional(isArrayOf(isString)),
});

const registrationStreamEventDecoder = createObjectDecoder<ElevenLabsRegistrationStreamEventDTO>("ElevenLabs registration stream event", {
  message: isOptional(isString),
  code: isOptional(isString),
  ok: isOptional(isBoolean),
  email: isOptional(isString),
  password: isOptional(isString),
  authenticated: isOptional(isBoolean),
  final_url: isOptional(isString),
  connection: isOptional(isString),
  status: isOptional(isNumber),
});

const nullableNumber = (value: unknown): boolean => value === null || isNumber(value);
const registrationAccountValidator = hasShape({
  id: isString,
  email: isString,
  password: isString,
  authenticated: isBoolean,
  apiKeyConfigured: isBoolean,
  status: isString,
  tier: isString,
  quotaUsed: nullableNumber,
  quotaLimit: nullableNumber,
  quotaRemaining: nullableNumber,
  quotaResetAt: isString,
  quotaUpdatedAt: isString,
  createdAt: isString,
  updatedAt: isString,
});
const registrationAccountsDecoder = createValidatedDecoder<ElevenLabsRegistrationAccountDTO[]>(
  "ElevenLabs registration accounts",
  isArrayOf(registrationAccountValidator),
);
const registrationAccountDecoder = createValidatedDecoder<ElevenLabsRegistrationAccountDTO>(
  "ElevenLabs registration account",
  registrationAccountValidator,
);

export function getElevenLabsStatus(): Promise<ElevenLabsStatusDTO> {
  return apiRequest("/api/admin/v1/elevenlabs/status", {}, statusDecoder);
}

export function getElevenLabsModels(): Promise<ElevenLabsModelListDTO> {
  return apiRequest("/api/admin/v1/elevenlabs/models", {}, modelListDecoder);
}

export function getElevenLabsRuntimeConfig(): Promise<ElevenLabsRuntimeConfigDTO> {
  return apiRequest("/api/admin/v1/elevenlabs/runtime-config", {}, runtimeConfigDecoder);
}

export function updateElevenLabsRuntimeConfig(input: ElevenLabsRuntimeConfigInput): Promise<ElevenLabsRuntimeConfigDTO> {
  return apiRequest("/api/admin/v1/elevenlabs/runtime-config", { method: "PUT", body: input }, runtimeConfigDecoder);
}

export function preflightElevenLabsRuntime(): Promise<ElevenLabsRuntimePreflightDTO> {
  return apiRequest("/api/admin/v1/elevenlabs/runtime-config/preflight", { method: "POST", body: {} }, runtimePreflightDecoder);
}

export function getElevenLabsRegistrationStatus(): Promise<ElevenLabsRegistrationStatusDTO> {
  return apiRequest("/api/admin/v1/elevenlabs/registration/status", {}, registrationStatusDecoder);
}

export function getElevenLabsRegistrationAccounts(): Promise<ElevenLabsRegistrationAccountDTO[]> {
  return apiRequest("/api/admin/v1/elevenlabs/registration/accounts", {}, registrationAccountsDecoder);
}

export function refreshElevenLabsRegistrationAccount(identifier: string): Promise<ElevenLabsRegistrationAccountDTO> {
  return apiRequest(
    `/api/admin/v1/elevenlabs/registration/accounts/${encodeURIComponent(identifier)}/refresh`,
    { method: "POST", body: {} },
    registrationAccountDecoder,
  );
}

export function runElevenLabsRegistrationAction(action: "preflight" | "dry-run" | "run"): Promise<ElevenLabsRegistrationResultDTO> {
  return apiRequest(`/api/admin/v1/elevenlabs/registration/${action}`, { method: "POST", body: {} }, registrationResultDecoder);
}

export async function streamElevenLabsRegistrationAction(
  action: "preflight" | "dry-run" | "run",
  onLog: (message: string) => void,
): Promise<ElevenLabsRegistrationResultDTO> {
  let result: ElevenLabsRegistrationResultDTO | undefined;
  await apiEventStream(
    `/api/admin/v1/elevenlabs/registration/${action}`,
    { method: "POST", headers: { Accept: "text/event-stream" }, body: {} },
    registrationStreamEventDecoder,
    ({ event, data }) => {
      if (event === "log" && data.message) {
        onLog(data.message);
        return;
      }
      if (event === "error") {
        throw new ApiError(502, data.code || "registration_failed", data.message || "ElevenLabs registration failed");
      }
      if (event === "complete" && data.ok === true) {
        result = {
          ok: true,
          email: data.email,
          password: data.password,
          authenticated: data.authenticated,
          finalURL: data.final_url,
          connection: data.connection,
          status: data.status,
        };
      }
    },
  );
  if (!result) {
    throw new ApiError(502, "registration_stream_incomplete", "Registration stream ended before the task completed");
  }
  return result;
}

export function generateElevenLabsSound(input: SoundGenerationInput): Promise<Blob> {
  return apiBlobRequest("/api/admin/v1/elevenlabs/sound-generation", { method: "POST", body: input });
}

export function generateElevenLabsImage(input: ImageGenerationInput): Promise<ImageGenerationDTO> {
  return apiRequest("/api/admin/v1/elevenlabs/images/generations", { method: "POST", body: input }, imageGenerationDecoder);
}
