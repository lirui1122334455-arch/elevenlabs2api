import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { AudioLines, CircleAlert, CircleCheck, Clipboard, Eye, EyeOff, FlaskConical, Image as ImageIcon, KeyRound, Network, RefreshCw, Save, ServerCog, Settings2, Sparkles, SquareTerminal, Trash2, Upload, UserPlus, Waves, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Table, TableActionCell, TableActionHead, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  generateElevenLabsImage,
  generateElevenLabsSound,
  getElevenLabsRegistrationAccounts,
  getElevenLabsRegistrationStatus,
  importElevenLabsOutlookAccounts,
  getElevenLabsModels,
  getElevenLabsRuntimeConfig,
  getElevenLabsStatus,
  preflightElevenLabsRuntime,
  refreshElevenLabsRegistrationAccount,
  streamElevenLabsRegistrationAction,
  updateElevenLabsRuntimeConfig,
  type ElevenLabsRuntimeConfigDTO,
  type ElevenLabsRegistrationAccountDTO,
  type ImageGenerationInput,
  type ElevenLabsRuntimeConfigInput,
} from "@/features/elevenlabs/elevenlabs-api";
import { CopyButton } from "@/shared/components/copy-button";
import { cn } from "@/shared/lib/cn";
import { formatDateTime, formatNumber } from "@/shared/lib/format";

const SOUND_FALLBACK_MODELS = ["eleven_text_to_sound_v2", "eleven_text_to_sound_v3"];
const ASPECT_RATIOS = ["auto", "3:1", "21:9", "2:1", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16", "1:2", "1:3"];
const RESOLUTIONS: ImageGenerationInput["resolution"][] = ["1K", "2K", "4K"];
const QUALITIES: ImageGenerationInput["quality"][] = ["low", "medium", "high"];
const IMAGE_MODES = ["text", "reference"] as const;
const MAX_REFERENCE_IMAGE_BYTES = 8 * 1024 * 1024;

type SoundForm = {
  text: string;
  model: string;
  duration: number;
  influence: number;
  loop: boolean;
  outputFormat: string;
};

type ImageForm = {
  prompt: string;
  aspectRatio: string;
  resolution: ImageGenerationInput["resolution"];
  quality: ImageGenerationInput["quality"];
  mode: typeof IMAGE_MODES[number];
};

type ReferenceImage = {
  name: string;
  mimeType: "image/png" | "image/jpeg" | "image/webp";
  contentBase64: string;
};

export function ElevenLabsPage() {
  const { t } = useTranslation();
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-medium">ElevenLabs Console</h1>
        <p className="mt-1 text-xs text-muted-foreground">{t("elevenLabs.subtitle")}</p>
      </header>
      <Tabs defaultValue="gateway" className="min-w-0">
        <TabsList>
          <TabsTrigger value="gateway"><ServerCog className="mr-1.5 size-3.5" />{t("elevenLabs.workspace.gateway")}</TabsTrigger>
          <TabsTrigger value="registration"><UserPlus className="mr-1.5 size-3.5" />{t("elevenLabs.workspace.registration")}</TabsTrigger>
          <TabsTrigger value="runtime"><Settings2 className="mr-1.5 size-3.5" />{t("elevenLabs.workspace.runtime")}</TabsTrigger>
        </TabsList>
        <TabsContent value="gateway" className="mt-7"><GatewayWorkspace /></TabsContent>
        <TabsContent value="registration" className="mt-7"><RegistrationWorkspace /></TabsContent>
        <TabsContent value="runtime" className="mt-7"><RuntimeConfigWorkspace /></TabsContent>
      </Tabs>
    </div>
  );
}

function GatewayWorkspace() {
  const { t } = useTranslation();
  const [sound, setSound] = useState<SoundForm>({
    text: "",
    model: "eleven_text_to_sound_v2",
    duration: 4,
    influence: 0.3,
    loop: false,
    outputFormat: "mp3_44100_128",
  });
  const [image, setImage] = useState<ImageForm>({ prompt: "", aspectRatio: "1:1", resolution: "1K", quality: "medium", mode: "text" });
  const [referenceImage, setReferenceImage] = useState<ReferenceImage | null>(null);
  const referenceInputRef = useRef<HTMLInputElement>(null);
  const [audioURL, setAudioURL] = useState("");

  const statusQuery = useQuery({
    queryKey: ["elevenlabs", "status"],
    queryFn: getElevenLabsStatus,
    refetchInterval: 10_000,
  });
  const modelsQuery = useQuery({
    queryKey: ["elevenlabs", "models"],
    queryFn: getElevenLabsModels,
    enabled: statusQuery.data?.reachable === true,
  });
  const soundModels = useMemo(() => {
    const values = modelsQuery.data?.data.map((model) => model.id).filter((id) => id.startsWith("eleven_text_to_sound")) ?? [];
    return values.length > 0 ? values : SOUND_FALLBACK_MODELS;
  }, [modelsQuery.data]);

  const soundMutation = useMutation({
    mutationFn: () => generateElevenLabsSound({
      text: sound.text.trim(), model: sound.model, duration_seconds: sound.duration,
      prompt_influence: sound.influence, loop: sound.loop, output_format: sound.outputFormat,
    }),
    onSuccess: (blob) => {
      setAudioURL(URL.createObjectURL(blob));
      toast.success(t("elevenLabs.soundReady"));
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("errors.generic")),
  });
  const imageMutation = useMutation({
    mutationFn: () => generateElevenLabsImage({
      model: "gpt-image-2", prompt: image.prompt.trim(), aspect_ratio: image.aspectRatio,
      resolution: image.resolution, quality: image.quality, response_format: "url",
      images: image.mode === "reference" && referenceImage ? [{
        type: "inline_base64", content_base64: referenceImage.contentBase64, mime_type: referenceImage.mimeType,
      }] : undefined,
    }),
    onSuccess: () => toast.success(t("elevenLabs.imageReady")),
    onError: (error) => toast.error(error instanceof Error ? error.message : t("errors.generic")),
  });

  useEffect(() => () => {
    if (audioURL) URL.revokeObjectURL(audioURL);
  }, [audioURL]);

  const status = statusQuery.data;
  const ready = status?.reachable === true && status.configured;
  const modelCount = modelsQuery.data?.data.length ?? 0;

  function refresh(): void {
    void Promise.all([statusQuery.refetch(), modelsQuery.refetch()]);
  }

  async function selectReferenceImage(file: File | undefined): Promise<void> {
    if (!file) return;
    if (!(["image/png", "image/jpeg", "image/webp"] as string[]).includes(file.type)) {
      toast.error(t("elevenLabs.referenceUnsupported"));
      return;
    }
    if (file.size <= 0 || file.size > MAX_REFERENCE_IMAGE_BYTES) {
      toast.error(t("elevenLabs.referenceTooLarge"));
      return;
    }
    try {
      const dataURL = await readFileAsDataURL(file);
      const contentBase64 = dataURL.slice(dataURL.indexOf(",") + 1);
      setReferenceImage({ name: file.name, mimeType: file.type as ReferenceImage["mimeType"], contentBase64 });
      imageMutation.reset();
    } catch {
      toast.error(t("errors.generic"));
    }
  }

  return (
    <div className="space-y-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-medium">{t("elevenLabs.workspace.gatewayTitle")}</h2>
          <p className="mt-1 text-xs text-muted-foreground">{t("elevenLabs.workspace.gatewayDescription")}</p>
        </div>
        <Button variant="ghost" size="icon" className="size-8 text-muted-foreground" onClick={refresh} disabled={statusQuery.isFetching || modelsQuery.isFetching} aria-label={t("common.refresh")}>
          <RefreshCw className={cn((statusQuery.isFetching || modelsQuery.isFetching) && "animate-spin")} />
        </Button>
      </header>

      <section className="grid border-y sm:grid-cols-2 xl:grid-cols-4 sm:[&>*:nth-child(even)]:border-l xl:[&>*+*]:border-l">
        <StatusMetric icon={<Network />} label={t("elevenLabs.gateway")} value={status?.reachable ? t("elevenLabs.online") : t("elevenLabs.offline")} healthy={status?.reachable === true} loading={statusQuery.isPending} />
        <StatusMetric icon={<KeyRound />} label={t("elevenLabs.credential")} value={status?.configured ? t("elevenLabs.accountPool", { count: status.accountPoolSize }) : t("elevenLabs.notConfigured")} healthy={status?.configured === true} loading={statusQuery.isPending} />
        <StatusMetric icon={<Waves />} label={t("elevenLabs.proxy")} value={status?.proxyConfigured ? t("elevenLabs.configured") : t("elevenLabs.direct")} healthy={status?.reachable === true} loading={statusQuery.isPending} />
        <StatusMetric icon={<Sparkles />} label={t("elevenLabs.models")} value={String(modelCount)} healthy={modelCount > 0} loading={modelsQuery.isPending && status?.reachable === true} />
      </section>

      {!ready ? (
        <div className="flex min-h-11 items-start gap-3 border-l-2 border-destructive/70 bg-destructive/5 px-4 py-3 text-xs text-muted-foreground">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
          <span>{status?.error || t(status?.reachable ? "elevenLabs.apiKeyRequired" : "elevenLabs.gatewayUnavailable")}</span>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 border-b pb-4">
        <span className="mr-1 text-xs text-muted-foreground">{t("elevenLabs.models")}</span>
        {(modelsQuery.data?.data ?? []).map((model) => <Badge key={model.id} variant="outline" className="font-mono">{model.id}</Badge>)}
        {status?.reachable && !modelsQuery.isPending && modelCount === 0 ? <span className="text-xs text-muted-foreground">{t("elevenLabs.noModels")}</span> : null}
      </div>

      <Tabs defaultValue="sound" className="min-w-0">
        <TabsList>
          <TabsTrigger value="sound"><AudioLines className="mr-1.5 size-3.5" />{t("elevenLabs.sound")}</TabsTrigger>
          <TabsTrigger value="image"><ImageIcon className="mr-1.5 size-3.5" />{t("elevenLabs.image")}</TabsTrigger>
        </TabsList>

        <TabsContent value="sound" className="mt-6">
          <div className="grid min-w-0 gap-8 xl:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
            <form className="space-y-5" onSubmit={(event) => { event.preventDefault(); soundMutation.mutate(); }}>
              <Field label={t("elevenLabs.prompt")} htmlFor="elevenlabs-sound-prompt">
                <Textarea id="elevenlabs-sound-prompt" className="min-h-32" maxLength={2_000} value={sound.text} onChange={(event) => setSound((current) => ({ ...current, text: event.target.value }))} placeholder={t("elevenLabs.soundPlaceholder")} />
              </Field>
              <Field label={t("elevenLabs.model")} htmlFor="elevenlabs-sound-model">
                <Select value={sound.model} onValueChange={(model) => setSound((current) => ({ ...current, model }))}>
                  <SelectTrigger id="elevenlabs-sound-model"><SelectValue /></SelectTrigger>
                  <SelectContent>{soundModels.map((model) => <SelectItem key={model} value={model}>{model}</SelectItem>)}</SelectContent>
                </Select>
              </Field>
              <RangeField id="elevenlabs-sound-duration" label={t("elevenLabs.duration")} value={sound.duration} min={0.5} max={30} step={0.5} suffix="s" onChange={(duration) => setSound((current) => ({ ...current, duration }))} />
              <RangeField id="elevenlabs-sound-influence" label={t("elevenLabs.influence")} value={sound.influence} min={0} max={1} step={0.05} onChange={(influence) => setSound((current) => ({ ...current, influence }))} />
              <div className="flex h-9 items-center justify-between border-y">
                <Label htmlFor="elevenlabs-sound-loop">{t("elevenLabs.loop")}</Label>
                <Switch id="elevenlabs-sound-loop" checked={sound.loop} onCheckedChange={(loop) => setSound((current) => ({ ...current, loop }))} />
              </div>
              <Button type="submit" size="sm" disabled={!ready || !sound.text.trim() || soundMutation.isPending}>
                {soundMutation.isPending ? <Spinner /> : <AudioLines />}{t(soundMutation.isPending ? "elevenLabs.generating" : "elevenLabs.generateSound")}
              </Button>
            </form>

            <OutputSurface icon={<AudioLines />} title={t("elevenLabs.soundOutput")} empty={!audioURL} emptyLabel={t("elevenLabs.noAudio")}>
              {audioURL ? <audio className="w-full max-w-2xl" controls preload="metadata" src={audioURL} /> : null}
            </OutputSurface>
          </div>
        </TabsContent>

        <TabsContent value="image" className="mt-6">
          <div className="grid min-w-0 gap-8 xl:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
            <form className="space-y-5" onSubmit={(event) => { event.preventDefault(); imageMutation.mutate(); }}>
              <SegmentedField label={t("elevenLabs.imageMode")} values={IMAGE_MODES} value={image.mode} format={(mode) => t(`elevenLabs.imageModes.${mode}`)} onChange={(mode) => { setImage((current) => ({ ...current, mode })); imageMutation.reset(); }} />
              <Field label={t("elevenLabs.prompt")} htmlFor="elevenlabs-image-prompt">
                <Textarea id="elevenlabs-image-prompt" className="min-h-32" maxLength={4_000} value={image.prompt} onChange={(event) => setImage((current) => ({ ...current, prompt: event.target.value }))} placeholder={t("elevenLabs.imagePlaceholder")} />
              </Field>
              {image.mode === "reference" ? (
                <Field label={t("elevenLabs.referenceImage")} htmlFor="elevenlabs-reference-image">
                  <input ref={referenceInputRef} id="elevenlabs-reference-image" className="hidden" type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => { void selectReferenceImage(event.target.files?.[0]); event.target.value = ""; }} />
                  {referenceImage ? (
                    <div className="flex min-w-0 items-center gap-3 border-y py-3">
                      <img src={`data:${referenceImage.mimeType};base64,${referenceImage.contentBase64}`} alt="" className="size-16 shrink-0 rounded-md border object-cover" />
                      <span className="min-w-0 flex-1 truncate text-xs" title={referenceImage.name}>{referenceImage.name}</span>
                      <Tooltip><TooltipTrigger asChild><Button type="button" variant="ghost" size="icon" onClick={() => referenceInputRef.current?.click()} aria-label={t("elevenLabs.replaceReference")}><Upload /></Button></TooltipTrigger><TooltipContent>{t("elevenLabs.replaceReference")}</TooltipContent></Tooltip>
                      <Tooltip><TooltipTrigger asChild><Button type="button" variant="ghost" size="icon" onClick={() => { setReferenceImage(null); imageMutation.reset(); }} aria-label={t("elevenLabs.removeReference")}><X /></Button></TooltipTrigger><TooltipContent>{t("elevenLabs.removeReference")}</TooltipContent></Tooltip>
                    </div>
                  ) : (
                    <Button type="button" variant="secondary" size="sm" onClick={() => referenceInputRef.current?.click()}><Upload />{t("elevenLabs.uploadReference")}</Button>
                  )}
                </Field>
              ) : null}
              <Field label={t("elevenLabs.aspectRatio")} htmlFor="elevenlabs-aspect-ratio">
                <Select value={image.aspectRatio} onValueChange={(aspectRatio) => setImage((current) => ({ ...current, aspectRatio }))}>
                  <SelectTrigger id="elevenlabs-aspect-ratio"><SelectValue /></SelectTrigger>
                  <SelectContent>{ASPECT_RATIOS.map((ratio) => <SelectItem key={ratio} value={ratio}>{ratio}</SelectItem>)}</SelectContent>
                </Select>
              </Field>
              <SegmentedField label={t("elevenLabs.resolution")} values={RESOLUTIONS} value={image.resolution} onChange={(resolution) => setImage((current) => ({ ...current, resolution }))} />
              <SegmentedField label={t("elevenLabs.quality")} values={QUALITIES} value={image.quality} format={(value) => t(`elevenLabs.qualityValues.${value}`)} onChange={(quality) => setImage((current) => ({ ...current, quality }))} />
              <div className="flex flex-wrap items-center gap-2">
                <Button type="submit" size="sm" disabled={!ready || !image.prompt.trim() || (image.mode === "reference" && !referenceImage) || imageMutation.isPending}>
                  {imageMutation.isPending ? <Spinner /> : <Sparkles />}{t(imageMutation.isPending ? "elevenLabs.generating" : "elevenLabs.generateImage")}
                </Button>
              </div>
            </form>

            <OutputSurface icon={<ImageIcon />} title={t("elevenLabs.imageOutput")} empty={!imageMutation.data?.data.length} emptyLabel={t("elevenLabs.noImages")}>
              <div className="grid w-full gap-3 sm:grid-cols-2">
                {(imageMutation.data?.data ?? []).map((item, index) => item.url ? (
                  <a key={item.url} href={item.url} target="_blank" rel="noreferrer" className="group flex aspect-video min-w-0 items-center justify-center overflow-hidden rounded-md border bg-secondary/30">
                    <img src={item.url} alt={item.revised_prompt || t("elevenLabs.generatedImage", { index: index + 1 })} className="size-full object-contain transition-transform group-hover:scale-[1.01]" />
                  </a>
                ) : null)}
              </div>
            </OutputSurface>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

const MAX_REGISTRATION_COUNT = 20;

function RegistrationWorkspace() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [count, setCount] = useState(1);
  const [outlookText, setOutlookText] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const [revealedPasswords, setRevealedPasswords] = useState<Set<string>>(() => new Set());
  const logViewportRef = useRef<HTMLDivElement>(null);
  const statusQuery = useQuery({
    queryKey: ["elevenlabs", "registration-status"],
    queryFn: getElevenLabsRegistrationStatus,
    refetchInterval: 5_000,
  });
  const accountsQuery = useQuery({
    queryKey: ["elevenlabs", "registration-accounts"],
    queryFn: getElevenLabsRegistrationAccounts,
  });
  const outlookImportMutation = useMutation({
    mutationFn: () => importElevenLabsOutlookAccounts(outlookText),
    onSuccess: (pool) => {
      setOutlookText("");
      toast.success(t("elevenLabs.registration.outlookImported", { count: pool.imported ?? 0, available: pool.available }));
      void queryClient.invalidateQueries({ queryKey: ["elevenlabs", "registration-status"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("errors.generic")),
  });
  const quotaMutation = useMutation({
    mutationFn: refreshElevenLabsRegistrationAccount,
    onSuccess: (account) => {
      queryClient.setQueryData<ElevenLabsRegistrationAccountDTO[]>(
        ["elevenlabs", "registration-accounts"],
        (current) => (current ?? []).map((item) => item.id === account.id ? account : item),
      );
      void queryClient.invalidateQueries({ queryKey: ["elevenlabs", "status"] });
      void queryClient.invalidateQueries({ queryKey: ["elevenlabs", "runtime-config"] });
      toast.success(t("elevenLabs.registration.quotaRefreshed"));
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("errors.generic")),
  });
  const actionMutation = useMutation({
    mutationFn: (action: "preflight" | "dry-run" | "run") => {
      setLogs([]);
      return streamElevenLabsRegistrationAction(action, (message) => {
        setLogs((current) => [...current, message].slice(-250));
      }, action === "run" ? count : 1);
    },
    onSuccess: (result, action) => {
      if (action === "run" && (result.requested ?? 1) > 1) {
        toast.success(t("elevenLabs.registration.batchReady", { succeeded: result.succeeded ?? 0, requested: result.requested ?? 0 }));
      } else {
        toast.success(t(`elevenLabs.registration.${action === "run" ? "registered" : "checkPassed"}`));
      }
      void queryClient.invalidateQueries({ queryKey: ["elevenlabs", "registration-status"] });
      if (action === "run") void queryClient.invalidateQueries({ queryKey: ["elevenlabs", "registration-accounts"] });
    },
    onError: (error, action) => {
      toast.error(error instanceof Error ? error.message : t("errors.generic"));
      if (action === "run") void queryClient.invalidateQueries({ queryKey: ["elevenlabs", "registration-accounts"] });
    },
  });
  const status = statusQuery.data;
  const ready = status?.reachable === true && status.captchaConfigured && status.mailConfigured;
  const captchaProviderLabel = status?.captchaProvider === "captcha_gateway" ? "Captcha Gateway" : "YesCaptcha";
  const result = actionMutation.data;
  const accounts = accountsQuery.data ?? [];

  useEffect(() => {
    const viewport = logViewportRef.current;
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }, [logs]);

  async function copyCredentials(): Promise<void> {
    const accounts = result?.accounts?.length
      ? result.accounts
      : result?.email && result.password
        ? [{ email: result.email, password: result.password, authenticated: result.authenticated === true }]
        : [];
    if (accounts.length === 0) return;
    await navigator.clipboard.writeText(accounts.map((account) => `${account.email}\t${account.password}`).join("\n"));
    toast.success(t("common.copied"));
  }

  function togglePassword(identifier: string): void {
    setRevealedPasswords((current) => {
      const next = new Set(current);
      if (next.has(identifier)) next.delete(identifier);
      else next.add(identifier);
      return next;
    });
  }

  return (
    <div className="space-y-7">
      <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(260px,0.38fr)_minmax(0,1fr)] lg:items-stretch">
        <div>
          <h2 className="text-base font-medium">{t("elevenLabs.registration.title")}</h2>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{t("elevenLabs.registration.description")}</p>
        </div>
        <section className="flex h-48 min-w-0 flex-col overflow-hidden rounded-md border bg-zinc-950 text-zinc-300" aria-label={t("elevenLabs.registration.liveLogs")}>
          <header className="flex h-10 shrink-0 items-center justify-between gap-3 border-b border-white/10 px-3">
            <div className="flex min-w-0 items-center gap-2">
              <SquareTerminal className="size-3.5 shrink-0 text-zinc-400" />
              <h3 className="truncate text-xs font-medium text-zinc-200">{t("elevenLabs.registration.liveLogs")}</h3>
              {actionMutation.isPending ? <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-emerald-400" aria-label={t("elevenLabs.registration.streaming")} /> : null}
            </div>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button type="button" variant="ghost" size="icon" className="size-7 text-zinc-400 hover:bg-white/10 hover:text-zinc-100" disabled={logs.length === 0} onClick={() => setLogs([])} aria-label={t("elevenLabs.registration.clearLogs")}>
                  <Trash2 className="size-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{t("elevenLabs.registration.clearLogs")}</TooltipContent>
            </Tooltip>
          </header>
          <div ref={logViewportRef} role="log" aria-live="polite" className="min-h-0 flex-1 overflow-auto px-3 py-2.5 font-mono text-[11px] leading-5">
            {logs.length > 0 ? logs.map((line, index) => <div key={`${index}-${line}`} className={cn("break-words whitespace-pre-wrap", line.startsWith("[phase:failed]") ? "text-red-300" : "text-zinc-300")}>{line}</div>) : <p className="font-sans text-xs text-zinc-500">{t("elevenLabs.registration.waitingForLogs")}</p>}
          </div>
        </section>
      </div>

      <section className="grid border-y sm:grid-cols-3 sm:[&>*+*]:border-l">
        <StatusMetric icon={<ServerCog />} label={t("elevenLabs.registration.service")} value={status?.reachable ? t("elevenLabs.online") : t("elevenLabs.offline")} healthy={status?.reachable === true} loading={statusQuery.isPending} />
        <StatusMetric icon={<KeyRound />} label={captchaProviderLabel} value={status?.captchaConfigured ? t("elevenLabs.configured") : t("elevenLabs.notConfigured")} healthy={status?.captchaConfigured === true} loading={statusQuery.isPending} />
        <StatusMetric icon={<Network />} label={status?.mailProvider === "outlook" ? "Outlook IMAP" : "YYDS Mail"} value={status?.mailConfigured ? (status.mailProvider === "outlook" ? t("elevenLabs.registration.outlookAvailable", { count: status.outlookPool?.available ?? 0 }) : t("elevenLabs.configured")) : t("elevenLabs.notConfigured")} healthy={status?.mailConfigured === true} loading={statusQuery.isPending} />
      </section>

      {!ready ? (
        <div className="flex min-h-11 items-start gap-3 border-l-2 border-destructive/70 bg-destructive/5 px-4 py-3 text-xs text-muted-foreground">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
          <span>{status?.error || t("elevenLabs.registration.notReady")}</span>
        </div>
      ) : null}

      <section className="space-y-3 border-b pb-6">
        <div>
          <h3 className="text-sm font-medium">{t("elevenLabs.registration.outlookTitle")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{t("elevenLabs.registration.outlookDescription")}</p>
        </div>
        <Textarea className="min-h-28 font-mono text-xs" value={outlookText} onChange={(event) => setOutlookText(event.target.value)} placeholder="email----password----client_id----refresh_token" />
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" disabled={!outlookText.trim() || outlookImportMutation.isPending} onClick={() => outlookImportMutation.mutate()}>
            {outlookImportMutation.isPending ? <Spinner /> : <Upload />}{t("elevenLabs.registration.outlookImport")}
          </Button>
          <span className="text-xs text-muted-foreground">{t("elevenLabs.registration.outlookPool", { available: status?.outlookPool?.available ?? 0, total: status?.outlookPool?.total ?? 0 })}</span>
        </div>
      </section>

      <div className="flex flex-wrap items-end gap-3 border-b pb-6">
        <div className="space-y-1.5">
          <Label htmlFor="elevenlabs-register-count">{t("elevenLabs.registration.count")}</Label>
          <Input
            id="elevenlabs-register-count"
            className="h-8 w-24"
            type="number"
            min={1}
            max={MAX_REGISTRATION_COUNT}
            value={count}
            disabled={actionMutation.isPending}
            onChange={(event) => {
              const next = Number(event.target.value);
              if (!Number.isFinite(next)) return;
              setCount(Math.min(MAX_REGISTRATION_COUNT, Math.max(1, Math.trunc(next))));
            }}
          />
        </div>
        <Button variant="secondary" size="sm" disabled={!ready || actionMutation.isPending} onClick={() => actionMutation.mutate("preflight")}>
          {actionMutation.isPending && actionMutation.variables === "preflight" ? <Spinner /> : <Network />}{t("elevenLabs.registration.preflight")}
        </Button>
        <Button variant="secondary" size="sm" disabled={!ready || actionMutation.isPending} onClick={() => actionMutation.mutate("dry-run")}>
          {actionMutation.isPending && actionMutation.variables === "dry-run" ? <Spinner /> : <FlaskConical />}{t("elevenLabs.registration.dryRun")}
        </Button>
        <Button size="sm" disabled={!ready || actionMutation.isPending || status?.running} onClick={() => setConfirmOpen(true)}>
          {actionMutation.isPending && actionMutation.variables === "run" ? <Spinner /> : <UserPlus />}{count > 1 ? t("elevenLabs.registration.registerMany", { count }) : t("elevenLabs.registration.registerOne")}
        </Button>
        <span className="text-xs text-muted-foreground">{t("elevenLabs.registration.connection", { value: status?.connection || "direct" })}</span>
      </div>

      {result?.accounts && result.accounts.length > 0 ? (
        <section className="space-y-3 border-l-2 border-emerald-500/70 bg-emerald-500/5 px-4 py-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">{t("elevenLabs.registration.batchReady", { succeeded: result.succeeded ?? result.accounts.length, requested: result.requested ?? result.accounts.length })}</p>
              <p className="mt-1 text-xs text-muted-foreground">{t("elevenLabs.registration.batchHint")}</p>
            </div>
            <Button variant="ghost" size="icon" className="size-8" onClick={() => void copyCredentials()} aria-label={t("common.copy")}><Clipboard /></Button>
          </div>
          <div className="space-y-2">
            {result.accounts.map((account) => (
              <div key={account.email} className="min-w-0">
                <p className="truncate font-mono text-xs">{account.email}</p>
                <p className="truncate font-mono text-xs text-muted-foreground">{account.password}</p>
              </div>
            ))}
          </div>
          {(result.failures ?? []).length > 0 ? (
            <p className="text-xs text-destructive">{t("elevenLabs.registration.batchFailed", { count: result.failed ?? result.failures?.length ?? 0 })}</p>
          ) : null}
        </section>
      ) : result?.email && result.password ? (
        <section className="space-y-3 border-l-2 border-emerald-500/70 bg-emerald-500/5 px-4 py-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">{t("elevenLabs.registration.accountReady")}</p>
              <p className="mt-1 font-mono text-xs text-muted-foreground">{result.email}</p>
            </div>
            <Button variant="ghost" size="icon" className="size-8" onClick={() => void copyCredentials()} aria-label={t("common.copy")}><Clipboard /></Button>
          </div>
          <p className="font-mono text-xs text-muted-foreground">{result.password}</p>
        </section>
      ) : null}

      <section className="space-y-3">
        <header className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-medium">{t("elevenLabs.registration.accountsTitle")}</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">{t("elevenLabs.registration.accountsCount", { count: accounts.length })}</p>
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" className="size-8 text-muted-foreground" disabled={accountsQuery.isFetching} onClick={() => void accountsQuery.refetch()} aria-label={t("common.refresh")}>
                <RefreshCw className={cn("size-4", accountsQuery.isFetching && "animate-spin")} />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{t("common.refresh")}</TooltipContent>
          </Tooltip>
        </header>
        <div className="divide-y border-y md:hidden">
          {accountsQuery.isPending ? (
            <div className="flex h-20 items-center justify-center"><Spinner /></div>
          ) : accountsQuery.isError ? (
            <div className="flex min-h-20 items-center justify-center px-3 py-4 text-center text-xs text-destructive">{accountsQuery.error instanceof Error ? accountsQuery.error.message : t("errors.generic")}</div>
          ) : accounts.length === 0 ? (
            <div className="flex h-20 items-center justify-center px-3 text-center text-xs text-muted-foreground">{t("elevenLabs.registration.noAccounts")}</div>
          ) : accounts.map((account) => {
            const revealed = revealedPasswords.has(account.id);
            const refreshing = quotaMutation.isPending && quotaMutation.variables === account.id;
            return (
              <div className="space-y-4 py-4" key={account.id}>
                <div className="flex min-w-0 items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[11px] text-muted-foreground">{t("elevenLabs.registration.email")}</p>
                    <div className="mt-1 flex min-w-0 items-center gap-1">
                      <span className="truncate font-mono text-xs" title={account.email}>{account.email}</span>
                      <CopyButton value={account.email} copyLabel={t("elevenLabs.registration.copyEmail")} />
                    </div>
                  </div>
                  <RegistrationRefreshButton refreshing={refreshing} onRefresh={() => quotaMutation.mutate(account.id)} t={t} />
                </div>
                <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-3 border-t pt-3">
                  <div className="min-w-0">
                    <p className="text-[11px] text-muted-foreground">{t("elevenLabs.registration.password")}</p>
                    <RegistrationPassword account={account} revealed={revealed} onToggle={() => togglePassword(account.id)} t={t} />
                  </div>
                  <div className="min-w-0">
                    <p className="text-[11px] text-muted-foreground">{t("elevenLabs.registration.plan")}</p>
                    <RegistrationPlan account={account} t={t} />
                  </div>
                </div>
                <div className="border-t pt-3">
                  <p className="mb-2 text-[11px] text-muted-foreground">{t("elevenLabs.registration.quota")}</p>
                  <RegistrationQuota account={account} locale={i18n.language} t={t} />
                </div>
                <dl className="grid grid-cols-2 gap-4 border-t pt-3">
                  <div className="min-w-0"><dt className="text-[11px] text-muted-foreground">{t("elevenLabs.registration.resetAt")}</dt><dd className="mt-1 truncate text-xs">{formatDateTime(account.quotaResetAt, i18n.language)}</dd></div>
                  <div className="min-w-0"><dt className="text-[11px] text-muted-foreground">{t("elevenLabs.registration.createdAt")}</dt><dd className="mt-1 truncate text-xs">{formatDateTime(account.createdAt, i18n.language)}</dd></div>
                </dl>
              </div>
            );
          })}
        </div>
        <div className="hidden border-y md:block">
          <Table className="min-w-[1040px] table-fixed">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[220px]">{t("elevenLabs.registration.email")}</TableHead>
                <TableHead className="w-[220px]">{t("elevenLabs.registration.password")}</TableHead>
                <TableHead className="w-[120px]">{t("elevenLabs.registration.plan")}</TableHead>
                <TableHead className="w-[260px]">{t("elevenLabs.registration.quota")}</TableHead>
                <TableHead className="w-[170px]">{t("elevenLabs.registration.resetAt")}</TableHead>
                <TableHead className="w-[170px]">{t("elevenLabs.registration.createdAt")}</TableHead>
                <TableActionHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {accountsQuery.isPending ? (
                <TableRow><TableCell colSpan={7} className="h-20 text-center"><Spinner /></TableCell></TableRow>
              ) : accountsQuery.isError ? (
                <TableRow><TableCell colSpan={7} className="h-20 text-center text-xs text-destructive">{accountsQuery.error instanceof Error ? accountsQuery.error.message : t("errors.generic")}</TableCell></TableRow>
              ) : accounts.length === 0 ? (
                <TableRow><TableCell colSpan={7} className="h-20 text-center text-xs text-muted-foreground">{t("elevenLabs.registration.noAccounts")}</TableCell></TableRow>
              ) : accounts.map((account) => {
                const revealed = revealedPasswords.has(account.id);
                const refreshing = quotaMutation.isPending && quotaMutation.variables === account.id;
                return (
                  <TableRow className="group" key={account.id}>
                    <TableCell>
                      <div className="flex min-w-0 items-center gap-1">
                        <span className="truncate font-mono text-xs" title={account.email}>{account.email}</span>
                        <CopyButton value={account.email} copyLabel={t("elevenLabs.registration.copyEmail")} />
                      </div>
                    </TableCell>
                    <TableCell>
                      <RegistrationPassword account={account} revealed={revealed} onToggle={() => togglePassword(account.id)} t={t} />
                    </TableCell>
                    <TableCell>
                      <RegistrationPlan account={account} t={t} />
                    </TableCell>
                    <TableCell><RegistrationQuota account={account} locale={i18n.language} t={t} /></TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(account.quotaResetAt, i18n.language)}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(account.createdAt, i18n.language)}</TableCell>
                    <TableActionCell>
                      <RegistrationRefreshButton refreshing={refreshing} onRefresh={() => quotaMutation.mutate(account.id)} t={t} />
                    </TableActionCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </section>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{count > 1 ? t("elevenLabs.registration.confirmManyTitle", { count }) : t("elevenLabs.registration.confirmTitle")}</DialogTitle>
            <DialogDescription>{t(count > 1 ? "elevenLabs.registration.confirmManyDescription" : "elevenLabs.registration.confirmDescription", { provider: captchaProviderLabel, count })}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" size="sm" onClick={() => setConfirmOpen(false)}>{t("common.cancel")}</Button>
            <Button size="sm" onClick={() => { setConfirmOpen(false); actionMutation.mutate("run"); }}><UserPlus />{count > 1 ? t("elevenLabs.registration.registerMany", { count }) : t("elevenLabs.registration.registerOne")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function RegistrationPassword({ account, revealed, onToggle, t }: { account: ElevenLabsRegistrationAccountDTO; revealed: boolean; onToggle: () => void; t: TFunction }) {
  const label = t(revealed ? "elevenLabs.registration.hidePassword" : "elevenLabs.registration.showPassword");
  return (
    <div className="mt-1 flex min-w-0 items-center gap-1">
      <span className="min-w-0 flex-1 truncate font-mono text-xs" title={revealed ? account.password : undefined}>{revealed ? account.password : "************"}</span>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon" className="size-7 shrink-0 text-muted-foreground" onClick={onToggle} aria-label={label}>
            {revealed ? <EyeOff /> : <Eye />}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
      <CopyButton value={account.password} copyLabel={t("elevenLabs.registration.copyPassword")} />
    </div>
  );
}

function RegistrationPlan({ account, t }: { account: ElevenLabsRegistrationAccountDTO; t: TFunction }) {
  return (
    <div className="mt-1 space-y-1">
      <Badge variant="outline" className="max-w-full truncate">{account.tier || t("elevenLabs.registration.planUnknown")}</Badge>
      <p className={cn("text-[11px]", account.authenticated ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground")}>{account.status || t("elevenLabs.registration.statusUnknown")}</p>
      <p className={cn("text-[11px]", account.apiKeyConfigured ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400")}>{t(account.apiKeyConfigured ? "elevenLabs.registration.gatewayConnected" : "elevenLabs.registration.gatewayPending")}</p>
    </div>
  );
}

function RegistrationRefreshButton({ refreshing, onRefresh, t }: { refreshing: boolean; onRefresh: () => void; t: TFunction }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" className="size-8 shrink-0" disabled={refreshing} onClick={onRefresh} aria-label={t("elevenLabs.registration.refreshQuota")}>
          {refreshing ? <Spinner /> : <RefreshCw />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{t("elevenLabs.registration.refreshQuota")}</TooltipContent>
    </Tooltip>
  );
}

function RegistrationQuota({ account, locale, t }: { account: ElevenLabsRegistrationAccountDTO; locale: string; t: TFunction }) {
  if (account.quotaUsed === null || account.quotaLimit === null || account.quotaRemaining === null) {
    return <span className="text-xs text-muted-foreground">{t("elevenLabs.registration.quotaPending")}</span>;
  }
  const percent = account.quotaLimit > 0 ? Math.min(100, Math.max(0, (account.quotaUsed / account.quotaLimit) * 100)) : 0;
  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="truncate">{t("elevenLabs.registration.quotaRemaining", { remaining: formatNumber(account.quotaRemaining, locale, 0), limit: formatNumber(account.quotaLimit, locale, 0) })}</span>
        <span className="shrink-0 tabular-nums text-muted-foreground">{formatNumber(percent, locale, 1)}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-emerald-500" style={{ width: `${percent}%` }} /></div>
      <p className="truncate text-[11px] text-muted-foreground">{t("elevenLabs.registration.quotaUsed", { value: formatNumber(account.quotaUsed, locale, 0) })}{account.quotaUpdatedAt ? ` · ${formatDateTime(account.quotaUpdatedAt, locale)}` : ""}</p>
    </div>
  );
}

const EMPTY_RUNTIME_FORM: ElevenLabsRuntimeConfigInput = {
  apiKey: "", clearAPIKey: false, apiBaseURL: "https://api.us.elevenlabs.io", proxyURL: "", dynamicProxyAPI: "", clearDynamicProxyAPI: false,
  requestTimeout: 60, generationTimeout: 240, registrationTimeout: 600,
  captchaProvider: "yescaptcha",
  yesCaptchaAPIKey: "", clearYesCaptchaAPIKey: false, yesCaptchaEndpoint: "https://api.yescaptcha.com",
  captchaGatewayAPIKey: "", clearCaptchaGatewayAPIKey: false, captchaGatewayEndpoint: "https://sub.aixiangshu.com",
  yydsAPIKey: "", clearYYDSAPIKey: false, yydsAPIBase: "https://maliapi.215.im/v1", mailProvider: "yyds", mailDomains: "",
};

function RuntimeConfigWorkspace() {
  const configQuery = useQuery({ queryKey: ["elevenlabs", "runtime-config"], queryFn: getElevenLabsRuntimeConfig });
  if (configQuery.isPending) return <div className="flex min-h-72 items-center justify-center"><Spinner /></div>;
  if (!configQuery.data) return <div className="border-l-2 border-destructive/70 px-4 py-3 text-xs text-muted-foreground">{configQuery.error instanceof Error ? configQuery.error.message : "ElevenLabs runtime configuration is unavailable."}</div>;
  return <RuntimeConfigForm key={configQuery.data.revision} config={configQuery.data} />;
}

function RuntimeConfigForm({ config }: { config: ElevenLabsRuntimeConfigDTO }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ElevenLabsRuntimeConfigInput>(() => ({
    ...EMPTY_RUNTIME_FORM,
    apiBaseURL: config.apiBaseURL,
    dynamicProxyAPI: "",
    requestTimeout: config.requestTimeout,
    generationTimeout: config.generationTimeout,
    registrationTimeout: config.registrationTimeout,
    captchaProvider: config.captchaProvider,
    yesCaptchaEndpoint: config.yesCaptchaEndpoint,
    captchaGatewayEndpoint: config.captchaGatewayEndpoint,
    yydsAPIBase: config.yydsAPIBase,
    mailProvider: config.mailProvider,
    mailDomains: config.mailDomains,
  }));
  const saveMutation = useMutation({
    mutationFn: () => updateElevenLabsRuntimeConfig(form),
    onSuccess: (data) => {
      queryClient.setQueryData(["elevenlabs", "runtime-config"], data);
      setForm((current) => ({ ...current, apiKey: "", yesCaptchaAPIKey: "", captchaGatewayAPIKey: "", yydsAPIKey: "" }));
      toast.success(t("elevenLabs.runtime.saved"));
      void queryClient.invalidateQueries({ queryKey: ["elevenlabs"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : t("errors.generic")),
  });
  const preflightMutation = useMutation({
    mutationFn: preflightElevenLabsRuntime,
    onSuccess: (result) => toast.success(t(result.ready ? "elevenLabs.runtime.ready" : "elevenLabs.runtime.checkCompleted")),
    onError: (error) => toast.error(error instanceof Error ? error.message : t("errors.generic")),
  });
  return (
    <div className="space-y-7">
      <div>
        <h2 className="text-base font-medium">{t("elevenLabs.runtime.title")}</h2>
        <p className="mt-1 text-xs text-muted-foreground">{t("elevenLabs.runtime.description")}</p>
      </div>

      <section className="grid border-y sm:grid-cols-3 sm:[&>*+*]:border-l">
        <StatusMetric icon={<KeyRound />} label="ElevenLabs API" value={config.apiKeyConfigured ? t("elevenLabs.configured") : t("elevenLabs.notConfigured")} healthy={config.apiKeyConfigured} loading={false} />
        <StatusMetric
          icon={<KeyRound />}
          label={config.captchaProvider === "captcha_gateway" ? "Captcha Gateway" : "YesCaptcha"}
          value={(config.captchaProvider === "captcha_gateway" ? config.captchaGatewayKeyConfigured : config.yesCaptchaKeyConfigured) ? t("elevenLabs.configured") : t("elevenLabs.notConfigured")}
          healthy={config.captchaProvider === "captcha_gateway" ? config.captchaGatewayKeyConfigured : config.yesCaptchaKeyConfigured}
          loading={false}
        />
        <StatusMetric icon={<Network />} label="YYDS Mail" value={config.yydsKeyConfigured ? t("elevenLabs.configured") : t("elevenLabs.notConfigured")} healthy={config.yydsKeyConfigured} loading={false} />
      </section>

      <form className="space-y-8" onSubmit={(event) => { event.preventDefault(); saveMutation.mutate(); }}>
        <ConfigSection title={t("elevenLabs.runtime.gatewaySection")}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="ElevenLabs API Key" htmlFor="elevenlabs-runtime-api-key">
              <Input id="elevenlabs-runtime-api-key" type="password" autoComplete="off" value={form.apiKey} onChange={(event) => setForm((current) => ({ ...current, apiKey: event.target.value }))} placeholder={config?.apiKeyConfigured ? t("elevenLabs.runtime.keepSecret") : "xi-api-key"} />
            </Field>
            <Field label={t("elevenLabs.runtime.apiBaseURL")} htmlFor="elevenlabs-runtime-api-base">
              <Input id="elevenlabs-runtime-api-base" value={form.apiBaseURL} onChange={(event) => setForm((current) => ({ ...current, apiBaseURL: event.target.value }))} />
            </Field>
            <Field label={t("elevenLabs.runtime.proxyURL")} htmlFor="elevenlabs-runtime-proxy">
              <Input id="elevenlabs-runtime-proxy" value={form.proxyURL} onChange={(event) => setForm((current) => ({ ...current, proxyURL: event.target.value }))} placeholder={config?.proxyConfigured && !config.dynamicProxyConfigured ? config.proxyLabel : t("elevenLabs.runtime.directPlaceholder")} />
            </Field>
            <Field label={t("elevenLabs.runtime.dynamicProxyAPI")} htmlFor="elevenlabs-runtime-dynamic-proxy">
              <Input id="elevenlabs-runtime-dynamic-proxy" value={form.dynamicProxyAPI} onChange={(event) => setForm((current) => ({ ...current, dynamicProxyAPI: event.target.value }))} placeholder={config?.dynamicProxyConfigured ? t("elevenLabs.runtime.keepSecret") : "https://white.1024proxy.com/white/api?region=Rand&num=1&time=10&format=1&type=txt"} />
            </Field>
            <Field label={t("elevenLabs.runtime.requestTimeout")} htmlFor="elevenlabs-runtime-request-timeout">
              <Input id="elevenlabs-runtime-request-timeout" type="number" min={5} max={300} value={form.requestTimeout} onChange={(event) => setForm((current) => ({ ...current, requestTimeout: Number(event.target.value) }))} />
            </Field>
            <Field label={t("elevenLabs.runtime.generationTimeout")} htmlFor="elevenlabs-runtime-generation-timeout">
              <Input id="elevenlabs-runtime-generation-timeout" type="number" min={30} max={900} value={form.generationTimeout} onChange={(event) => setForm((current) => ({ ...current, generationTimeout: Number(event.target.value) }))} />
            </Field>
          </div>
        </ConfigSection>

        <ConfigSection title={t("elevenLabs.runtime.registrationSection")}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("elevenLabs.runtime.captchaProvider")} htmlFor="elevenlabs-runtime-captcha-provider">
              <Select value={form.captchaProvider} onValueChange={(captchaProvider: "yescaptcha" | "captcha_gateway") => setForm((current) => ({ ...current, captchaProvider }))}>
                <SelectTrigger id="elevenlabs-runtime-captcha-provider"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="yescaptcha">YesCaptcha</SelectItem>
                  <SelectItem value="captcha_gateway">Captcha Gateway</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <div className="hidden sm:block" aria-hidden="true" />
            {form.captchaProvider === "captcha_gateway" ? (
              <>
                <Field label="Captcha Gateway API Key" htmlFor="elevenlabs-runtime-captcha-gateway-key">
                  <Input id="elevenlabs-runtime-captcha-gateway-key" type="password" autoComplete="off" value={form.captchaGatewayAPIKey} onChange={(event) => setForm((current) => ({ ...current, captchaGatewayAPIKey: event.target.value }))} placeholder={config?.captchaGatewayKeyConfigured ? t("elevenLabs.runtime.keepSecret") : undefined} />
                </Field>
                <Field label="Captcha Gateway Endpoint" htmlFor="elevenlabs-runtime-captcha-gateway-endpoint">
                  <Input id="elevenlabs-runtime-captcha-gateway-endpoint" value={form.captchaGatewayEndpoint} onChange={(event) => setForm((current) => ({ ...current, captchaGatewayEndpoint: event.target.value }))} />
                </Field>
              </>
            ) : (
              <>
                <Field label="YesCaptcha API Key" htmlFor="elevenlabs-runtime-captcha-key">
                  <Input id="elevenlabs-runtime-captcha-key" type="password" autoComplete="off" value={form.yesCaptchaAPIKey} onChange={(event) => setForm((current) => ({ ...current, yesCaptchaAPIKey: event.target.value }))} placeholder={config?.yesCaptchaKeyConfigured ? t("elevenLabs.runtime.keepSecret") : undefined} />
                </Field>
                <Field label="YesCaptcha Endpoint" htmlFor="elevenlabs-runtime-captcha-endpoint">
                  <Input id="elevenlabs-runtime-captcha-endpoint" value={form.yesCaptchaEndpoint} onChange={(event) => setForm((current) => ({ ...current, yesCaptchaEndpoint: event.target.value }))} />
                </Field>
              </>
            )}
            <Field label={t("elevenLabs.runtime.mailProvider")} htmlFor="elevenlabs-runtime-mail-provider">
              <Select value={form.mailProvider} onValueChange={(mailProvider: "yyds" | "outlook") => setForm((current) => ({ ...current, mailProvider }))}>
                <SelectTrigger id="elevenlabs-runtime-mail-provider"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="yyds">YYDS Mail</SelectItem>
                  <SelectItem value="outlook">Outlook IMAP</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="YYDS API Key" htmlFor="elevenlabs-runtime-yyds-key">
              <Input id="elevenlabs-runtime-yyds-key" type="password" autoComplete="off" value={form.yydsAPIKey} onChange={(event) => setForm((current) => ({ ...current, yydsAPIKey: event.target.value }))} placeholder={config?.yydsKeyConfigured ? t("elevenLabs.runtime.keepSecret") : "AC-..."} />
            </Field>
            <Field label="YYDS API Base" htmlFor="elevenlabs-runtime-yyds-base">
              <Input id="elevenlabs-runtime-yyds-base" value={form.yydsAPIBase} onChange={(event) => setForm((current) => ({ ...current, yydsAPIBase: event.target.value }))} />
            </Field>
            <Field label={t("elevenLabs.runtime.mailDomains")} htmlFor="elevenlabs-runtime-domains">
              <Input id="elevenlabs-runtime-domains" value={form.mailDomains} onChange={(event) => setForm((current) => ({ ...current, mailDomains: event.target.value }))} placeholder="318ai.top, 88.mivioo.xyz" />
            </Field>
            <Field label={t("elevenLabs.runtime.registrationTimeout")} htmlFor="elevenlabs-runtime-registration-timeout">
              <Input id="elevenlabs-runtime-registration-timeout" type="number" min={60} max={1800} value={form.registrationTimeout} onChange={(event) => setForm((current) => ({ ...current, registrationTimeout: Number(event.target.value) }))} />
            </Field>
          </div>
        </ConfigSection>

        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" size="sm" disabled={saveMutation.isPending}>{saveMutation.isPending ? <Spinner /> : <Save />}{t("common.save")}</Button>
          <Button type="button" variant="secondary" size="sm" disabled={preflightMutation.isPending} onClick={() => preflightMutation.mutate()}>{preflightMutation.isPending ? <Spinner /> : <FlaskConical />}{t("elevenLabs.runtime.preflight")}</Button>
          <span className="text-xs text-muted-foreground">{t("elevenLabs.runtime.connection", { value: config?.proxyLabel || "direct" })}</span>
        </div>
      </form>

      {preflightMutation.data ? <PreflightResult result={preflightMutation.data} /> : null}
    </div>
  );
}

function ConfigSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="border-t pt-5"><h3 className="mb-4 text-sm font-medium">{title}</h3>{children}</section>;
}

function PreflightResult({ result }: { result: Awaited<ReturnType<typeof preflightElevenLabsRuntime>> }) {
  const { t } = useTranslation();
  const rows = [
    [t("elevenLabs.runtime.connectionLabel"), result.connection.ok === true ? "passed" : "failed"],
    ["ElevenLabs API Key", result.api_key.configured !== true ? "notConfigured" : result.api_key.valid === true ? "passed" : "failed"],
    [result.captcha.provider === "captcha_gateway" ? "Captcha Gateway" : "YesCaptcha", result.captcha.valid === true ? "passed" : "failed"],
    ["YYDS Mail", result.yyds.valid === true ? "passed" : "failed"],
  ] as const;
  return (
    <section className="border-y">
      {rows.map(([label, state]) => {
        const passed = state === "passed";
        const failed = state === "failed";
        const value = passed ? t("elevenLabs.runtime.passed") : failed ? t("elevenLabs.runtime.failed") : t("elevenLabs.notConfigured");
        return <div key={label} className="flex h-10 items-center justify-between border-t px-3 text-xs first:border-t-0"><span>{label}</span><span className={cn("flex items-center gap-1.5", passed ? "text-emerald-700 dark:text-emerald-400" : failed ? "text-destructive" : "text-muted-foreground")}>{passed ? <CircleCheck className="size-3.5" /> : <CircleAlert className="size-3.5" />}{value}</span></div>;
      })}
    </section>
  );
}

function StatusMetric({ icon, label, value, healthy, loading }: { icon: ReactNode; label: string; value: string; healthy: boolean; loading: boolean }) {
  return (
    <div className="flex min-h-20 items-center gap-3 px-4 py-3">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground [&_svg]:size-4">{icon}</span>
      <span className="min-w-0">
        <span className="block text-xs text-muted-foreground">{label}</span>
        <span className={cn("mt-1 flex min-h-5 items-center gap-1.5 truncate text-sm font-medium", healthy ? "text-emerald-700 dark:text-emerald-400" : "text-foreground")}>
          {loading ? <Spinner className="size-3.5" /> : healthy ? <CircleCheck className="size-3.5 shrink-0" /> : null}{value}
        </span>
      </span>
    </div>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return <div className="space-y-2"><Label htmlFor={htmlFor}>{label}</Label>{children}</div>;
}

function RangeField({ id, label, value, min, max, step, suffix = "", onChange }: { id: string; label: string; value: number; min: number; max: number; step: number; suffix?: string; onChange: (value: number) => void }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between"><Label htmlFor={id}>{label}</Label><span className="w-12 text-right text-xs tabular-nums text-foreground">{value}{suffix}</span></div>
      <input id={id} className="h-5 w-full cursor-pointer accent-foreground" type="range" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </div>
  );
}

function SegmentedField<T extends string>({ label, values, value, onChange, format = (item) => item }: { label: string; values: readonly T[]; value: T; onChange: (value: T) => void; format?: (value: T) => string }) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <div className="grid h-8 grid-flow-col overflow-hidden rounded-md border bg-secondary/40">
        {values.map((item) => <button key={item} type="button" className={cn("min-w-0 border-l px-2 text-xs first:border-l-0", item === value ? "bg-background font-medium text-foreground" : "text-muted-foreground hover:text-foreground")} aria-pressed={item === value} onClick={() => onChange(item)}>{format(item)}</button>)}
      </div>
    </div>
  );
}

function OutputSurface({ icon, title, empty, emptyLabel, children }: { icon: ReactNode; title: string; empty: boolean; emptyLabel: string; children: ReactNode }) {
  return (
    <section className="flex min-h-[320px] min-w-0 flex-col border-t pt-6 xl:border-l xl:border-t-0 xl:pl-8 xl:pt-0">
      <div className="flex h-8 items-center gap-2 text-sm font-medium text-foreground [&_svg]:size-4">{icon}{title}</div>
      <div className="flex min-h-[264px] flex-1 items-center justify-center py-4">
        {empty ? <div className="flex flex-col items-center gap-2 text-xs text-muted-foreground"><span className="[&_svg]:size-7 [&_svg]:stroke-1">{icon}</span>{emptyLabel}</div> : children}
      </div>
    </section>
  );
}

function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("invalid image data"));
    reader.onerror = () => reject(reader.error ?? new Error("failed to read image"));
    reader.readAsDataURL(file);
  });
}
