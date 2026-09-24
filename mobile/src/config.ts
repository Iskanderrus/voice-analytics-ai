import { createApiClient } from './api/client';

// Demo configuration via Expo public env vars (see mobile/README section in the
// root README). A token baked into an app bundle is NOT a secret; a production
// app would obtain a per-user token through a login flow instead.
export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';
export const API_TOKEN = process.env.EXPO_PUBLIC_API_TOKEN ?? '';

export const POLL_INTERVAL_MS = 3000;

export const api = createApiClient(API_URL, API_TOKEN);
