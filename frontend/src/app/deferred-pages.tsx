import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from "react";

import { Spinner } from "@/components/ui/spinner";

const AppShell = lazyNamed(() => import("@/app/app-shell"), "AppShell");
const ElevenLabsPage = lazyNamed(() => import("@/features/elevenlabs/elevenlabs-page"), "ElevenLabsPage");

function lazyNamed<T extends Record<K, ComponentType>, K extends keyof T>(loader: () => Promise<T>, exportName: K): LazyExoticComponent<T[K]> {
  return lazy(async () => ({ default: (await loader())[exportName] }));
}

function DeferredPage({ page: Page }: { page: ComponentType }) {
  return <Suspense fallback={<PageLoadingFallback />}><Page /></Suspense>;
}

export function DeferredAppShell() {
  return <Suspense fallback={<PageLoadingFallback fullScreen />}><AppShell /></Suspense>;
}

export function DeferredElevenLabsPage() {
  return <DeferredPage page={ElevenLabsPage} />;
}

function PageLoadingFallback({ fullScreen = false }: { fullScreen?: boolean }) {
  return (
    <div className={fullScreen ? "flex min-h-screen items-center justify-center bg-background" : "flex min-h-[calc(100vh-7rem)] items-center justify-center"}>
      <Spinner className="size-5" />
    </div>
  );
}
