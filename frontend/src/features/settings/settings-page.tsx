import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, RotateCcw } from "lucide-react";
import { type ReactNode } from "react";
import { Controller } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EgressNodes } from "@/features/settings/egress-nodes";
import { getAutoRegisterStatus, runAutoRegisterOnce, stopAutoRegister } from "@/features/settings/settings-api";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { isByteSizeUnit, isDurationUnit, type ByteSizeValue, type DurationValue } from "@/features/settings/settings-model";
import { useSettings } from "@/features/settings/use-settings";
import { ErrorState } from "@/shared/components/data-state";

export function SettingsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { form, settingsQuery, updateMutation, reset } = useSettings();
  const autoStatusQuery = useQuery({
    queryKey: ["auto-register-status"],
    queryFn: getAutoRegisterStatus,
    // Poll faster while a job is running so phase/logs stay live.
    refetchInterval: (query) => (query.state.data?.running ? 1_500 : 5_000),
  });
  const runOnceMutation = useMutation({
    mutationFn: runAutoRegisterOnce,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["auto-register-status"] }),
  });
  const stopMutation = useMutation({
    mutationFn: stopAutoRegister,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["auto-register-status"] }),
  });

  if (settingsQuery.isError) {
    return <ErrorState message={settingsQuery.error.message} onRetry={() => void settingsQuery.refetch()} />;
  }

  const snapshot = settingsQuery.data;
  const loading = settingsQuery.isPending;
  const statsigMode = form.watch("providerWeb.statsigMode");
  const statsigManualConfigured = form.watch("providerWeb.statsigManualConfigured");
  const buildClientVersion = form.watch("providerBuild.clientVersion");
  const buildUserAgent = form.watch("providerBuild.userAgent");
  const recommendedBuild = snapshot?.recommendedProviderBuild;
  const recommendedBuildApplied = recommendedBuild != null
    && buildClientVersion === recommendedBuild.clientVersion
    && buildUserAgent === recommendedBuild.userAgent;
  const syncRecommendedBuild = () => {
    if (!recommendedBuild) return;
    form.setValue("providerBuild.clientVersion", recommendedBuild.clientVersion, { shouldDirty: true, shouldTouch: true, shouldValidate: true });
    form.setValue("providerBuild.userAgent", recommendedBuild.userAgent, { shouldDirty: true, shouldTouch: true, shouldValidate: true });
  };

  return (
    <form className="w-full space-y-8 [&_input]:border-transparent" onSubmit={form.handleSubmit((values) => updateMutation.mutate(values))}>
      <header className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-medium">{t("settings.title")}</h1>
          <p className="sr-only">{t("settings.description")}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button type="button" variant="ghost" size="icon" className="size-8" aria-label={t("common.reset")} disabled={loading || updateMutation.isPending || !form.formState.isDirty} onClick={reset}>
                <RotateCcw />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{t("common.reset")}</TooltipContent>
          </Tooltip>
          <Button type="submit" size="sm" disabled={loading || updateMutation.isPending || !form.formState.isDirty}>
            {updateMutation.isPending ? <Spinner /> : null}{t("common.save")}
          </Button>
        </div>
      </header>

      {loading ? <div className="flex min-h-64 items-center justify-center"><Spinner /></div> : null}
      {snapshot ? (
        <Tabs defaultValue="providers" className="space-y-6">
          <TabsList>
            <TabsTrigger value="providers">{t("settings.groups.providers")}</TabsTrigger>
            <TabsTrigger value="delivery">{t("settings.groups.delivery")}</TabsTrigger>
            <TabsTrigger value="policies">{t("settings.groups.policies")}</TabsTrigger>
            <TabsTrigger value="autoRegister">{t("settings.groups.autoRegister")}</TabsTrigger>
          </TabsList>

          <SettingsPane value="providers">
          <SettingsSection
            title={t("models.providerGrokBuild")}
            action={recommendedBuild ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button type="button" variant="secondary" size="sm" disabled={loading || updateMutation.isPending || recommendedBuildApplied} onClick={syncRecommendedBuild}>
                    <RefreshCw />{recommendedBuildApplied ? t("settings.provider.recommendedVersionApplied") : t("settings.provider.syncRecommendedVersion")}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{t("settings.provider.syncRecommendedVersionDescription")}</TooltipContent>
              </Tooltip>
            ) : undefined}
          >
            <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
              <SettingsField controlId="provider-base-url" className="sm:col-span-2" label={t("settings.provider.baseURL")} error={form.formState.errors.providerBuild?.baseURL?.message}><Input id="provider-base-url" {...form.register("providerBuild.baseURL")} /></SettingsField>
              <SettingsField controlId="provider-client-version" label={t("settings.provider.clientVersion")} badge={t("settings.provider.recommendedVersion", { version: recommendedBuild?.clientVersion ?? "-" })} error={form.formState.errors.providerBuild?.clientVersion?.message}><Input id="provider-client-version" {...form.register("providerBuild.clientVersion")} /></SettingsField>
              <SettingsField controlId="provider-client-identifier" label={t("settings.provider.clientIdentifier")} error={form.formState.errors.providerBuild?.clientIdentifier?.message}><Input id="provider-client-identifier" {...form.register("providerBuild.clientIdentifier")} /></SettingsField>
              <SettingsField controlId="provider-token-auth" label={t("settings.provider.tokenAuth")} badge={form.watch("providerBuild.tokenAuthConfigured") ? t("settings.web.statsigConfigured") : undefined} error={form.formState.errors.providerBuild?.tokenAuth?.message}><Input id="provider-token-auth" type="password" autoComplete="off" placeholder={form.watch("providerBuild.tokenAuthConfigured") ? t("settings.web.statsigKeepConfigured") : undefined} {...form.register("providerBuild.tokenAuth")} /></SettingsField>
              <SettingsField controlId="provider-user-agent" label={t("settings.provider.userAgent")} error={form.formState.errors.providerBuild?.userAgent?.message}><Input id="provider-user-agent" {...form.register("providerBuild.userAgent")} /></SettingsField>
            </div>
          </SettingsSection>

          <SettingsSection title={t("settings.web.title")}>
            <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
              <SettingsField controlId="web-base-url" className="sm:col-span-2" label={t("settings.web.baseURL")} error={form.formState.errors.providerWeb?.baseURL?.message}><Input id="web-base-url" {...form.register("providerWeb.baseURL")} /></SettingsField>
              <SettingsField controlId="web-statsig-mode" className="sm:col-span-2" label={t("settings.web.statsigMode")} error={form.formState.errors.providerWeb?.statsigMode?.message}>
                <Controller control={form.control} name="providerWeb.statsigMode" render={({ field }) => (
                  <div id="web-statsig-mode" role="radiogroup" className="grid h-8 grid-cols-2 rounded-md bg-muted/55 p-0.5">
                    <Button type="button" role="radio" size="sm" variant={field.value === "manual" ? "secondary" : "ghost"} className="h-7 text-xs shadow-none" aria-checked={field.value === "manual"} onClick={() => field.onChange("manual")}>{t("settings.web.statsigManual")}</Button>
                    <Button type="button" role="radio" size="sm" variant={field.value === "url" ? "secondary" : "ghost"} className="h-7 text-xs shadow-none" aria-checked={field.value === "url"} onClick={() => field.onChange("url")}>{t("settings.web.statsigURL")}</Button>
                  </div>
                )} />
              </SettingsField>
              {statsigMode === "manual" ? (
                <SettingsField controlId="web-statsig-manual" className="sm:col-span-2" label={t("settings.web.statsigValue")} badge={statsigManualConfigured ? t("settings.web.statsigConfigured") : undefined} error={form.formState.errors.providerWeb?.statsigManualValue?.message}>
                  <Input id="web-statsig-manual" type="password" autoComplete="off" placeholder={statsigManualConfigured ? t("settings.web.statsigKeepConfigured") : undefined} {...form.register("providerWeb.statsigManualValue")} />
                </SettingsField>
              ) : (
                <SettingsField controlId="web-statsig-url" className="sm:col-span-2" label={t("settings.web.statsigSignerURL")} error={form.formState.errors.providerWeb?.statsigSignerURL?.message}>
                  <Input id="web-statsig-url" type="url" placeholder="http://grok-signer-go:8788/sign" {...form.register("providerWeb.statsigSignerURL")} />
                </SettingsField>
              )}
              <SettingsField controlId="web-quota-timeout" label={t("settings.web.quotaTimeout")} error={form.formState.errors.providerWeb?.quotaTimeout?.message}><Controller control={form.control} name="providerWeb.quotaTimeout" render={({ field }) => <DurationInput id="web-quota-timeout" value={field.value} onChange={field.onChange} />} /></SettingsField>
              <SettingsField controlId="web-chat-timeout" label={t("settings.web.chatTimeout")} error={form.formState.errors.providerWeb?.chatTimeout?.message}><Controller control={form.control} name="providerWeb.chatTimeout" render={({ field }) => <DurationInput id="web-chat-timeout" value={field.value} onChange={field.onChange} />} /></SettingsField>
              <SettingsField controlId="web-image-timeout" label={t("settings.web.imageTimeout")} error={form.formState.errors.providerWeb?.imageTimeout?.message}><Controller control={form.control} name="providerWeb.imageTimeout" render={({ field }) => <DurationInput id="web-image-timeout" value={field.value} onChange={field.onChange} />} /></SettingsField>
              <SettingsField controlId="web-video-timeout" label={t("settings.web.videoTimeout")} error={form.formState.errors.providerWeb?.videoTimeout?.message}><Controller control={form.control} name="providerWeb.videoTimeout" render={({ field }) => <DurationInput id="web-video-timeout" value={field.value} onChange={field.onChange} />} /></SettingsField>
              <SettingsField controlId="web-media-concurrency" label={t("settings.web.mediaConcurrency")} badge={t("settings.restartRequired")} error={form.formState.errors.providerWeb?.mediaConcurrency?.message}><Input id="web-media-concurrency" type="number" min={1} max={64} {...form.register("providerWeb.mediaConcurrency", { valueAsNumber: true })} /></SettingsField>
              <SettingsField controlId="web-recovery-base" label={t("settings.web.recoveryBackoffBase")} error={form.formState.errors.providerWeb?.recoveryBackoffBase?.message}><Controller control={form.control} name="providerWeb.recoveryBackoffBase" render={({ field }) => <DurationInput id="web-recovery-base" value={field.value} onChange={field.onChange} />} /></SettingsField>
              <SettingsField controlId="web-recovery-max" label={t("settings.web.recoveryBackoffMax")} error={form.formState.errors.providerWeb?.recoveryBackoffMax?.message}><Controller control={form.control} name="providerWeb.recoveryBackoffMax" render={({ field }) => <DurationInput id="web-recovery-max" value={field.value} onChange={field.onChange} />} /></SettingsField>
              <SettingsField controlId="web-nsfw" label={t("settings.web.allowNSFW")}><Controller control={form.control} name="providerWeb.allowNSFW" render={({ field }) => <div className="flex h-8 items-center"><Switch id="web-nsfw" checked={field.value} onCheckedChange={field.onChange} /></div>} /></SettingsField>
            </div>
          </SettingsSection>

          <SettingsSection title={t("console.name")}>
            <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
              <SettingsField controlId="console-base-url" className="sm:col-span-2" label={t("console.baseURL")} error={form.formState.errors.providerConsole?.baseURL?.message}><Input id="console-base-url" type="url" {...form.register("providerConsole.baseURL")} /></SettingsField>
              <SettingsField controlId="console-user-agent" className="sm:col-span-2" label={t("console.userAgent")} error={form.formState.errors.providerConsole?.userAgent?.message}><Input id="console-user-agent" {...form.register("providerConsole.userAgent")} /></SettingsField>
              <SettingsField controlId="console-chat-timeout" label={t("console.chatTimeout")} error={form.formState.errors.providerConsole?.chatTimeout?.message}><Controller control={form.control} name="providerConsole.chatTimeout" render={({ field }) => <DurationInput id="console-chat-timeout" value={field.value} onChange={field.onChange} />} /></SettingsField>
            </div>
          </SettingsSection>
          </SettingsPane>

          <SettingsPane value="delivery">
          <SettingsSection title={t("settings.media.title")}>
            <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
              <SettingsField controlId="media-max-image-size" label={t("settings.media.maxImageSize")} error={form.formState.errors.media?.maxImageSize?.message}>
                <Controller control={form.control} name="media.maxImageSize" render={({ field }) => <ByteSizeInput id="media-max-image-size" value={field.value} onChange={field.onChange} />} />
              </SettingsField>
              <SettingsField controlId="media-max-total-size" label={t("settings.media.maxTotalSize")} error={form.formState.errors.media?.maxTotalSize?.message}>
                <Controller control={form.control} name="media.maxTotalSize" render={({ field }) => <ByteSizeInput id="media-max-total-size" value={field.value} onChange={field.onChange} />} />
              </SettingsField>
              <SettingsField controlId="media-cleanup-threshold" label={t("settings.media.cleanupThresholdPercent")} error={form.formState.errors.media?.cleanupThresholdPercent?.message}>
                <div className="flex min-w-0">
                  <Input id="media-cleanup-threshold" type="number" min={50} max={95} className="min-w-0 rounded-r-none" {...form.register("media.cleanupThresholdPercent", { valueAsNumber: true })} />
                  <div className="-ml-px flex h-8 w-24 shrink-0 items-center justify-center rounded-r-md bg-secondary/55 text-xs text-muted-foreground">%</div>
                </div>
              </SettingsField>
              <SettingsField controlId="media-cleanup-interval" label={t("settings.media.cleanupInterval")} error={form.formState.errors.media?.cleanupInterval?.message}>
                <Controller control={form.control} name="media.cleanupInterval" render={({ field }) => <DurationInput id="media-cleanup-interval" value={field.value} onChange={field.onChange} />} />
              </SettingsField>
              <SettingsField controlId="frontend-public-api-base-url" label={t("settings.media.publicApiBaseURL")} description={t("settings.media.publicApiBaseURLHelp")} error={form.formState.errors.frontend?.publicApiBaseURL?.message} className="sm:col-span-2">
                <Input id="frontend-public-api-base-url" placeholder="https://api.example.com" {...form.register("frontend.publicApiBaseURL")} />
              </SettingsField>
            </div>
          </SettingsSection>

          <SettingsSection title={t("settings.egress.title")} wide>
            <EgressNodes />
          </SettingsSection>
          </SettingsPane>

          <SettingsPane value="policies">
          <SettingsSection title={t("settings.server.title")}>
            <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
              <SettingsField controlId="server-max-concurrent-requests" label={t("settings.server.maxConcurrentRequests")} description={t("settings.server.maxConcurrentRequestsHelp")} error={form.formState.errors.server?.maxConcurrentRequests?.message}>
                <Input id="server-max-concurrent-requests" type="number" min={1} max={100_000} {...form.register("server.maxConcurrentRequests", { valueAsNumber: true })} />
              </SettingsField>
            </div>
          </SettingsSection>

          <SettingsSection title={t("settings.batch.title")}>
            <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
              <SettingsField controlId="batch-import-concurrency" label={t("settings.batch.importConcurrency")} error={form.formState.errors.batch?.importConcurrency?.message}><Input id="batch-import-concurrency" type="number" min={1} max={50} {...form.register("batch.importConcurrency", { valueAsNumber: true })} /></SettingsField>
              <SettingsField controlId="batch-conversion-concurrency" label={t("settings.batch.conversionConcurrency")} error={form.formState.errors.batch?.conversionConcurrency?.message}><Input id="batch-conversion-concurrency" type="number" min={1} max={50} {...form.register("batch.conversionConcurrency", { valueAsNumber: true })} /></SettingsField>
              <SettingsField controlId="batch-sync-concurrency" label={t("settings.batch.syncConcurrency")} error={form.formState.errors.batch?.syncConcurrency?.message}><Input id="batch-sync-concurrency" type="number" min={1} max={50} {...form.register("batch.syncConcurrency", { valueAsNumber: true })} /></SettingsField>
              <SettingsField controlId="batch-refresh-concurrency" label={t("settings.batch.refreshConcurrency")} error={form.formState.errors.batch?.refreshConcurrency?.message}><Input id="batch-refresh-concurrency" type="number" min={1} max={50} {...form.register("batch.refreshConcurrency", { valueAsNumber: true })} /></SettingsField>
              <SettingsField controlId="batch-random-delay" label={t("settings.batch.randomDelay")} error={form.formState.errors.batch?.randomDelay?.message}><Input id="batch-random-delay" type="number" min={0} max={5_000} step={10} {...form.register("batch.randomDelay", { valueAsNumber: true })} /></SettingsField>
            </div>
          </SettingsSection>

          <SettingsSection title={t("settings.routing.title")}>
            <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
              <SettingsField controlId="routing-sticky-ttl" label={t("settings.routing.stickyTTL")} error={form.formState.errors.routing?.stickyTTL?.message}><Controller control={form.control} name="routing.stickyTTL" render={({ field }) => <DurationInput id="routing-sticky-ttl" value={field.value} onChange={field.onChange} />} /></SettingsField>
              <SettingsField controlId="routing-cooldown-base" label={t("settings.routing.cooldownBase")} error={form.formState.errors.routing?.cooldownBase?.message}><Controller control={form.control} name="routing.cooldownBase" render={({ field }) => <DurationInput id="routing-cooldown-base" value={field.value} onChange={field.onChange} />} /></SettingsField>
              <SettingsField controlId="routing-cooldown-max" label={t("settings.routing.cooldownMax")} error={form.formState.errors.routing?.cooldownMax?.message}><Controller control={form.control} name="routing.cooldownMax" render={({ field }) => <DurationInput id="routing-cooldown-max" value={field.value} onChange={field.onChange} />} /></SettingsField>
              <SettingsField controlId="routing-capacity-wait" label={t("settings.routing.capacityWait", { defaultValue: "Saturated account wait" })} error={form.formState.errors.routing?.capacityWait?.message}><Controller control={form.control} name="routing.capacityWait" render={({ field }) => <DurationInput id="routing-capacity-wait" value={field.value} onChange={field.onChange} />} /></SettingsField>
              <SettingsField controlId="routing-max-attempts" label={t("settings.routing.maxAttempts")} error={form.formState.errors.routing?.maxAttempts?.message}><Input id="routing-max-attempts" type="number" min={1} max={10} {...form.register("routing.maxAttempts", { valueAsNumber: true })} /></SettingsField>
            </div>
          </SettingsSection>

          <SettingsSection title={t("settings.audit.title")}>
            <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
              <SettingsField controlId="audit-buffer-size" label={t("settings.audit.bufferSize")} badge={t("settings.restartRequired")} error={form.formState.errors.audit?.bufferSize?.message}><Input id="audit-buffer-size" type="number" min={1} max={262_144} {...form.register("audit.bufferSize", { valueAsNumber: true })} /></SettingsField>
              <SettingsField controlId="audit-batch-size" label={t("settings.audit.batchSize")} error={form.formState.errors.audit?.batchSize?.message}><Input id="audit-batch-size" type="number" min={1} max={4_096} {...form.register("audit.batchSize", { valueAsNumber: true })} /></SettingsField>
              <SettingsField controlId="audit-flush-interval" label={t("settings.audit.flushInterval")} error={form.formState.errors.audit?.flushInterval?.message}><Controller control={form.control} name="audit.flushInterval" render={({ field }) => <DurationInput id="audit-flush-interval" value={field.value} onChange={field.onChange} />} /></SettingsField>
            </div>
          </SettingsSection>

          <SettingsSection title={t("settings.clientKeys.title")}>
            <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
              <SettingsField controlId="client-key-default-rpm" label={t("settings.clientKeys.rpmLimit")} error={form.formState.errors.clientKeyDefaults?.rpmLimit?.message}><Input id="client-key-default-rpm" type="number" min={1} max={100_000} {...form.register("clientKeyDefaults.rpmLimit", { valueAsNumber: true })} /></SettingsField>
              <SettingsField controlId="client-key-default-concurrency" label={t("settings.clientKeys.maxConcurrent")} error={form.formState.errors.clientKeyDefaults?.maxConcurrent?.message}><Input id="client-key-default-concurrency" type="number" min={1} max={1_024} {...form.register("clientKeyDefaults.maxConcurrent", { valueAsNumber: true })} /></SettingsField>
            </div>
          </SettingsSection>
          </SettingsPane>

          <SettingsPane value="autoRegister">
          <SettingsSection
            title={t("settings.autoRegister.title")}
            action={
              autoStatusQuery.data?.running ? (
                <Button
                  type="button"
                  size="sm"
                  variant="destructive"
                  disabled={stopMutation.isPending || autoStatusQuery.data?.stopping}
                  onClick={() => stopMutation.mutate()}
                >
                  {stopMutation.isPending || autoStatusQuery.data?.stopping ? <Spinner /> : null}
                  {autoStatusQuery.data?.stopping ? t("settings.autoRegister.stopping") : t("settings.autoRegister.stop")}
                </Button>
              ) : (
                <Button type="button" size="sm" variant="secondary" disabled={runOnceMutation.isPending} onClick={() => runOnceMutation.mutate()}>
                  {runOnceMutation.isPending ? <Spinner /> : null}
                  {t("settings.autoRegister.runOnce")}
                </Button>
              )
            }
          >
            <p className="mb-4 text-xs leading-5 text-muted-foreground">{t("settings.autoRegister.description")}</p>
            <div className="mb-5 grid gap-2 rounded-md bg-muted/40 p-3 text-xs text-muted-foreground sm:grid-cols-2">
              <div>
                {t("settings.autoRegister.status")}:{" "}
                {autoStatusQuery.data?.stopping
                  ? t("settings.autoRegister.stopping")
                  : autoStatusQuery.data?.running
                    ? t("settings.autoRegister.running")
                    : t("settings.autoRegister.idle")}
                {typeof autoStatusQuery.data?.inFlight === "number" && autoStatusQuery.data.inFlight > 0
                  ? ` · ${t("settings.autoRegister.inFlight")}: ${autoStatusQuery.data.inFlight}`
                  : null}
              </div>
              <div>
                {t("settings.autoRegister.availableBuild")}: {autoStatusQuery.data?.availableBuild ?? "-"}
                {" · "}
                {t("settings.autoRegister.availableWeb")}: {autoStatusQuery.data?.availableWeb ?? "-"}
                {" · "}
                {t("settings.autoRegister.runtimeThreshold")}: {t("settings.autoRegister.targetShort")} {autoStatusQuery.data?.targetAvailableWeb ?? "-"}
                {form.watch("autoRegister.targetAvailableWeb") !== autoStatusQuery.data?.targetAvailableWeb
                  ? (
                    <span className="ml-1 text-amber-600 dark:text-amber-400">
                      ({t("settings.autoRegister.formDiffersFromRuntime")})
                    </span>
                  )
                  : null}
              </div>
              <div>{t("settings.autoRegister.success")}: {autoStatusQuery.data?.successCount ?? 0}</div>
              <div>{t("settings.autoRegister.failure")}: {autoStatusQuery.data?.failureCount ?? 0}</div>
              <div>{t("settings.autoRegister.lastEmail")}: {autoStatusQuery.data?.lastEmail || "-"}</div>
              <div>{t("settings.autoRegister.lastProxy")}: {autoStatusQuery.data?.lastProxy || "-"}</div>
              <div className="sm:col-span-2">
                {t("settings.autoRegister.phase")}:{" "}
                <span className="font-medium text-foreground">{autoStatusQuery.data?.phase || "-"}</span>
                {autoStatusQuery.data?.progress ? (
                  <span className="mt-0.5 block break-all text-[11px] leading-4">{autoStatusQuery.data.progress}</span>
                ) : null}
              </div>
              {autoStatusQuery.data?.lastError ? <div className="sm:col-span-2 text-destructive">{t("settings.autoRegister.lastError")}: {autoStatusQuery.data.lastError}</div> : null}
              {autoStatusQuery.data?.recentLogs && autoStatusQuery.data.recentLogs.length > 0 ? (
                <div className="sm:col-span-2">
                  <div className="mb-1 font-medium text-foreground">{t("settings.autoRegister.recentLogs")}</div>
                  <pre className="max-h-40 overflow-auto rounded border border-border/60 bg-background/70 p-2 text-[11px] leading-4 text-foreground/90 whitespace-pre-wrap break-all">
                    {autoStatusQuery.data.recentLogs.slice(-24).join("\n")}
                  </pre>
                </div>
              ) : null}
            </div>
            <div className="grid gap-x-4 gap-y-5 sm:grid-cols-2">
              <SettingsField controlId="auto-register-enabled" label={t("settings.autoRegister.enabled")} className="sm:col-span-2">
                <Controller control={form.control} name="autoRegister.enabled" render={({ field }) => (
                  <div className="flex h-8 items-center"><Switch id="auto-register-enabled" checked={field.value} onCheckedChange={field.onChange} /></div>
                )} />
              </SettingsField>
              <SettingsField controlId="auto-register-min" label={t("settings.autoRegister.minAvailableWeb")} description={t("settings.autoRegister.minAvailableWebHelp")} error={form.formState.errors.autoRegister?.minAvailableWeb?.message}>
                <Input id="auto-register-min" type="number" min={0} max={10_000} {...form.register("autoRegister.minAvailableWeb", { valueAsNumber: true })} />
              </SettingsField>
              <SettingsField controlId="auto-register-target" label={t("settings.autoRegister.targetAvailableWeb")} description={t("settings.autoRegister.targetAvailableWebHelp")} error={form.formState.errors.autoRegister?.targetAvailableWeb?.message}>
                <Input id="auto-register-target" type="number" min={0} max={10_000} {...form.register("autoRegister.targetAvailableWeb", { valueAsNumber: true })} />
              </SettingsField>
              <SettingsField controlId="auto-register-concurrency" label={t("settings.autoRegister.maxConcurrent")} error={form.formState.errors.autoRegister?.maxConcurrent?.message}>
                <Input id="auto-register-concurrency" type="number" min={1} max={5} {...form.register("autoRegister.maxConcurrent", { valueAsNumber: true })} />
              </SettingsField>
              <SettingsField controlId="auto-register-check-interval" label={t("settings.autoRegister.checkInterval")} error={form.formState.errors.autoRegister?.checkInterval?.message}>
                <Controller control={form.control} name="autoRegister.checkInterval" render={({ field }) => <DurationInput id="auto-register-check-interval" value={field.value} onChange={field.onChange} />} />
              </SettingsField>
              <SettingsField controlId="auto-register-timeout" label={t("settings.autoRegister.registerTimeout")} error={form.formState.errors.autoRegister?.registerTimeout?.message}>
                <Controller control={form.control} name="autoRegister.registerTimeout" render={({ field }) => <DurationInput id="auto-register-timeout" value={field.value} onChange={field.onChange} />} />
              </SettingsField>
              <SettingsField controlId="auto-register-sidecar" className="sm:col-span-2" label={t("settings.autoRegister.sidecarURL")} error={form.formState.errors.autoRegister?.sidecarURL?.message}>
                <Input id="auto-register-sidecar" placeholder="http://127.0.0.1:8091" {...form.register("autoRegister.sidecarURL")} />
              </SettingsField>
              <SettingsField controlId="auto-register-mail-provider" className="sm:col-span-2" label={t("settings.autoRegister.mailProvider")} error={form.formState.errors.autoRegister?.mailProvider?.message}>
                <Controller control={form.control} name="autoRegister.mailProvider" render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger id="auto-register-mail-provider"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="cloudflare">{t("settings.autoRegister.mailProviderCloudflare")}</SelectItem>
                      <SelectItem value="yyds">{t("settings.autoRegister.mailProviderYyds")}</SelectItem>
                    </SelectContent>
                  </Select>
                )} />
              </SettingsField>
              <SettingsField controlId="auto-register-mail-base" className="sm:col-span-2" label={t("settings.autoRegister.mailApiBase")} error={form.formState.errors.autoRegister?.mailApiBase?.message}>
                <Input
                  id="auto-register-mail-base"
                  placeholder={form.watch("autoRegister.mailProvider") === "yyds" ? "https://maliapi.215.im/v1" : "https://api-mail.example.com"}
                  {...form.register("autoRegister.mailApiBase")}
                />
                {form.watch("autoRegister.mailProvider") === "yyds" ? (
                  <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.autoRegister.mailApiBaseYydsHelp")}</p>
                ) : null}
              </SettingsField>
              <SettingsField controlId="auto-register-mail-key" className="sm:col-span-2" label={t("settings.autoRegister.mailAdminKey")} badge={form.watch("autoRegister.mailAdminKeyConfigured") ? t("settings.autoRegister.keepConfigured") : undefined} error={form.formState.errors.autoRegister?.mailAdminKey?.message}>
                <Input
                  id="auto-register-mail-key"
                  type="password"
                  autoComplete="off"
                  placeholder={form.watch("autoRegister.mailAdminKeyConfigured") ? t("settings.autoRegister.keepConfigured") : undefined}
                  {...form.register("autoRegister.mailAdminKey")}
                />
                {form.watch("autoRegister.mailProvider") === "yyds" ? (
                  <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.autoRegister.mailAdminKeyYydsHelp")}</p>
                ) : null}
              </SettingsField>
              {form.watch("autoRegister.mailProvider") === "yyds" ? (
                <SettingsField controlId="auto-register-yyds-jwt" className="sm:col-span-2" label={t("settings.autoRegister.yydsJwt")} badge={form.watch("autoRegister.yydsJwtConfigured") ? t("settings.autoRegister.keepConfigured") : undefined} error={form.formState.errors.autoRegister?.yydsJwt?.message}>
                  <Input
                    id="auto-register-yyds-jwt"
                    type="password"
                    autoComplete="off"
                    placeholder={form.watch("autoRegister.yydsJwtConfigured") ? t("settings.autoRegister.keepConfigured") : undefined}
                    {...form.register("autoRegister.yydsJwt")}
                  />
                  <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.autoRegister.yydsJwtHelp")}</p>
                </SettingsField>
              ) : null}
              {form.watch("autoRegister.mailProvider") !== "yyds" ? (
                <>
                  <SettingsField controlId="auto-register-mail-mode" label={t("settings.autoRegister.mailAuthMode")} error={form.formState.errors.autoRegister?.mailAuthMode?.message}>
                    <Input id="auto-register-mail-mode" {...form.register("autoRegister.mailAuthMode")} />
                  </SettingsField>
                  <SettingsField controlId="auto-register-mail-new" label={t("settings.autoRegister.mailPathNewAddress")} error={form.formState.errors.autoRegister?.mailPathNewAddress?.message}>
                    <Input id="auto-register-mail-new" {...form.register("autoRegister.mailPathNewAddress")} />
                  </SettingsField>
                  <SettingsField controlId="auto-register-mail-msg" label={t("settings.autoRegister.mailPathMessages")} error={form.formState.errors.autoRegister?.mailPathMessages?.message}>
                    <Input id="auto-register-mail-msg" {...form.register("autoRegister.mailPathMessages")} />
                  </SettingsField>
                </>
              ) : null}
              <SettingsField controlId="auto-register-mail-domains" className="sm:col-span-2" label={t("settings.autoRegister.mailDomains")} error={form.formState.errors.autoRegister?.mailDomains?.message}>
                <Input
                  id="auto-register-mail-domains"
                  {...form.register("autoRegister.mailDomains")}
                />
                {form.watch("autoRegister.mailProvider") === "yyds" ? (
                  <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.autoRegister.mailDomainsYydsHelp")}</p>
                ) : (
                  <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.autoRegister.mailDomainsCloudflareHelp")}</p>
                )}
              </SettingsField>
              <SettingsField controlId="auto-register-mail-strategy" label={t("settings.autoRegister.mailDomainStrategy")} error={form.formState.errors.autoRegister?.mailDomainStrategy?.message}>
                <Controller control={form.control} name="autoRegister.mailDomainStrategy" render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger id="auto-register-mail-strategy"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="rotate">{t("settings.autoRegister.mailDomainStrategyRotate")}</SelectItem>
                      <SelectItem value="random">{t("settings.autoRegister.mailDomainStrategyRandom")}</SelectItem>
                      <SelectItem value="first">{t("settings.autoRegister.mailDomainStrategyFirst")}</SelectItem>
                    </SelectContent>
                  </Select>
                )} />
              </SettingsField>
              {form.watch("autoRegister.mailProvider") !== "yyds" ? (
                <>
                  <SettingsField controlId="auto-register-mail-auto-domains" label={t("settings.autoRegister.mailAutoDomains")}>
                    <Controller control={form.control} name="autoRegister.mailAutoDomains" render={({ field }) => (
                      <div className="flex h-8 items-center"><Switch id="auto-register-mail-auto-domains" checked={field.value} onCheckedChange={field.onChange} /></div>
                    )} />
                    <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.autoRegister.mailAutoDomainsHelp")}</p>
                  </SettingsField>
                  <SettingsField controlId="auto-register-mail-random-sub" label={t("settings.autoRegister.mailRandomSubdomain")}>
                    <Controller control={form.control} name="autoRegister.mailRandomSubdomain" render={({ field }) => (
                      <div className="flex h-8 items-center"><Switch id="auto-register-mail-random-sub" checked={field.value} onCheckedChange={field.onChange} /></div>
                    )} />
                    <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.autoRegister.mailRandomSubdomainHelp")}</p>
                  </SettingsField>
                </>
              ) : (
                <SettingsField controlId="auto-register-yyds-public" className="sm:col-span-2" label={t("settings.autoRegister.yydsAllowPublicDomains")}>
                  <Controller control={form.control} name="autoRegister.yydsAllowPublicDomains" render={({ field }) => (
                    <div className="flex h-8 items-center"><Switch id="auto-register-yyds-public" checked={field.value} onCheckedChange={field.onChange} /></div>
                  )} />
                  <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.autoRegister.yydsAllowPublicDomainsHelp")}</p>
                </SettingsField>
              )}
              <SettingsField controlId="auto-register-captcha-key" className="sm:col-span-2" label={t("settings.autoRegister.captchaKey")} badge={form.watch("autoRegister.captchaKeyConfigured") ? t("settings.autoRegister.keepConfigured") : undefined} error={form.formState.errors.autoRegister?.captchaKey?.message}>
                <Input id="auto-register-captcha-key" type="password" autoComplete="off" placeholder={form.watch("autoRegister.captchaKeyConfigured") ? t("settings.autoRegister.keepConfigured") : undefined} {...form.register("autoRegister.captchaKey")} />
              </SettingsField>
              <SettingsField controlId="auto-register-captcha-endpoint" className="sm:col-span-2" label={t("settings.autoRegister.captchaEndpoint")} error={form.formState.errors.autoRegister?.captchaEndpoint?.message}>
                <Input id="auto-register-captcha-endpoint" placeholder="https://api.ez-captcha.com" {...form.register("autoRegister.captchaEndpoint")} />
              </SettingsField>
              <SettingsField controlId="auto-register-captcha-timeout" label={t("settings.autoRegister.captchaTimeout")} error={form.formState.errors.autoRegister?.captchaTimeout?.message}>
                <Controller control={form.control} name="autoRegister.captchaTimeout" render={({ field }) => <DurationInput id="auto-register-captcha-timeout" value={field.value} onChange={field.onChange} />} />
              </SettingsField>
              <SettingsField controlId="auto-register-mail-timeout" label={t("settings.autoRegister.mailTimeout")} error={form.formState.errors.autoRegister?.mailTimeout?.message}>
                <Controller control={form.control} name="autoRegister.mailTimeout" render={({ field }) => <DurationInput id="auto-register-mail-timeout" value={field.value} onChange={field.onChange} />} />
              </SettingsField>
              <SettingsField controlId="auto-register-fallback-proxy" className="sm:col-span-2" label={t("settings.autoRegister.fallbackProxyURL")} error={form.formState.errors.autoRegister?.fallbackProxyURL?.message}>
                <Input id="auto-register-fallback-proxy" placeholder="http://127.0.0.1:7897" {...form.register("autoRegister.fallbackProxyURL")} />
                <p className="mt-1 text-[11px] text-muted-foreground">{t("settings.autoRegister.fallbackProxyURLHelp")}</p>
              </SettingsField>
              <SettingsField controlId="auto-register-skip-captcha" label={t("settings.autoRegister.skipCaptcha")} className="sm:col-span-2">
                <Controller control={form.control} name="autoRegister.skipCaptcha" render={({ field }) => (
                  <div className="flex h-8 items-center"><Switch id="auto-register-skip-captcha" checked={field.value} onCheckedChange={field.onChange} /></div>
                )} />
              </SettingsField>
              <SettingsField controlId="auto-register-also-console" label={t("settings.autoRegister.alsoImportConsole")} className="sm:col-span-2">
                <Controller control={form.control} name="autoRegister.alsoImportConsole" render={({ field }) => (
                  <div className="flex h-8 items-center"><Switch id="auto-register-also-console" checked={field.value} onCheckedChange={field.onChange} /></div>
                )} />
              </SettingsField>
              <SettingsField controlId="auto-register-verify-build" label={t("settings.autoRegister.verifyBuildAfterRegister")} description={t("settings.autoRegister.verifyBuildAfterRegisterHelp")} className="sm:col-span-2">
                <Controller control={form.control} name="autoRegister.verifyBuildAfterRegister" render={({ field }) => (
                  <div className="flex h-8 items-center"><Switch id="auto-register-verify-build" checked={field.value} onCheckedChange={field.onChange} /></div>
                )} />
              </SettingsField>
              <SettingsField controlId="auto-register-probe-delay" label={t("settings.autoRegister.probeDelay")} description={t("settings.autoRegister.probeDelayHelp")}>
                <Controller control={form.control} name="autoRegister.probeDelay" render={({ field }) => (
                  <DurationInput id="auto-register-probe-delay" value={field.value} onChange={field.onChange} />
                )} />
              </SettingsField>
              <SettingsField controlId="auto-register-probe-model" label={t("settings.autoRegister.probeModel")} description={t("settings.autoRegister.probeModelHelp")}>
                <Controller control={form.control} name="autoRegister.probeModel" render={({ field }) => (
                  <Input id="auto-register-probe-model" value={field.value} onChange={field.onChange} />
                )} />
              </SettingsField>
            </div>
          </SettingsSection>
          </SettingsPane>
        </Tabs>
      ) : null}
    </form>
  );
}

function ByteSizeInput({ id, value, onChange }: { id: string; value?: ByteSizeValue; onChange: (value: ByteSizeValue) => void }) {
  const { t } = useTranslation();
  const unit = value?.unit ?? "MiB";
  return (
    <div className="flex min-w-0">
      <Input
        id={id}
        type="number"
        min="0.001"
        step="any"
        className="min-w-0 rounded-r-none"
        value={Number.isFinite(value?.value) ? value?.value : ""}
        onChange={(event) => onChange({ value: event.target.value === "" ? Number.NaN : Number(event.target.value), unit })}
      />
      <Select value={unit} onValueChange={(nextUnit) => { if (isByteSizeUnit(nextUnit)) onChange({ value: value?.value ?? 1, unit: nextUnit }); }}>
        <SelectTrigger className="-ml-px w-24 shrink-0 rounded-l-none border-transparent bg-secondary/55" aria-label={t("settings.media.sizeUnit")}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="MiB">MiB</SelectItem>
          <SelectItem value="GiB">GiB</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

function DurationInput({ id, value, onChange }: { id: string; value?: DurationValue; onChange: (value: DurationValue) => void }) {
  const { t } = useTranslation();
  const unit = value?.unit ?? "s";
  return (
    <div className="flex min-w-0">
      <Input
        id={id}
        type="number"
        min="0.001"
        step="any"
        className="min-w-0 rounded-r-none"
        value={Number.isFinite(value?.value) ? value?.value : ""}
        onChange={(event) => onChange({ value: event.target.value === "" ? Number.NaN : Number(event.target.value), unit })}
      />
      <Select value={unit} onValueChange={(nextUnit) => { if (isDurationUnit(nextUnit)) onChange({ value: value?.value ?? 1, unit: nextUnit }); }}>
        <SelectTrigger className="-ml-px w-24 shrink-0 rounded-l-none border-transparent bg-secondary/55" aria-label={t("settings.durationUnit")}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="s">{t("settings.units.seconds")}</SelectItem>
          <SelectItem value="m">{t("settings.units.minutes")}</SelectItem>
          <SelectItem value="h">{t("settings.units.hours")}</SelectItem>
          <SelectItem value="d">{t("settings.units.days")}</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

function SettingsPane({ value, children }: { value: string; children: ReactNode }) {
  return (
    <TabsContent value={value} forceMount className="m-0 space-y-8 data-[state=inactive]:hidden">
      {children}
    </TabsContent>
  );
}

function SettingsSection({ title, action, wide = false, children }: { title: string; action?: ReactNode; wide?: boolean; children: ReactNode }) {
  return (
    <section className="space-y-4">
      <div className="flex min-h-8 items-center justify-between gap-3">
        <h2 className="text-sm font-medium">{title}</h2>
        {action}
      </div>
      <div className={wide ? "min-w-0" : "min-w-0 max-w-[860px]"}>{children}</div>
    </section>
  );
}

function SettingsField({ controlId, label, badge, description, error, className, children }: { controlId: string; label: string; badge?: string; description?: string; error?: string; className?: string; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className={className}>
      <div className="mb-1.5 flex min-h-5 items-center gap-2">
        <Label htmlFor={controlId} className="text-xs font-medium">{label}</Label>
        {badge ? <span className="text-[11px] font-normal text-muted-foreground">{badge}</span> : null}
      </div>
      {children}
      {description ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p> : null}
      {error ? <p className="mt-1 text-xs text-destructive">{t("settings.invalidValue")}</p> : null}
    </div>
  );
}
