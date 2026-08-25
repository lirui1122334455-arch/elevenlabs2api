import { Navigate, createBrowserRouter } from "react-router-dom";

import { AnonymousBoundary, AuthBoundary } from "@/app/auth-boundary";
import { DeferredAppShell, DeferredElevenLabsPage } from "@/app/deferred-pages";
import { LoginPage } from "@/features/auth/login-page";

export const router = createBrowserRouter([
  {
    element: <AnonymousBoundary />,
    children: [{ path: "/login", element: <LoginPage /> }],
  },
  {
    element: <AuthBoundary />,
    children: [
      {
        element: <DeferredAppShell />,
        children: [
          { index: true, element: <Navigate to="/elevenlabs" replace /> },
          { path: "/elevenlabs", element: <DeferredElevenLabsPage /> },
        ],
      },
    ],
  },
  { path: "*", element: <Navigate to="/elevenlabs" replace /> },
]);
