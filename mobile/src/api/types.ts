// Mirrors the backend API contract (backend/apps/*/serializers.py). No domain
// logic lives here: the client only displays what the backend decides.

export type JobStatus =
  | 'CREATED'
  | 'QUEUED'
  | 'TRANSCRIBING'
  | 'ANALYSING_STANDARD'
  | 'ANALYSING_CUSTOM'
  | 'COMPLETED'
  | 'FAILED';

export type UploadSlot = {
  upload_id: string;
  object_key: string;
  upload_method: 'POST';
  upload_url: string;
  upload_fields: Record<string, string>;
  expires_at: string;
};

export type Upload = {
  id: string;
  original_filename: string;
  status: 'PENDING_UPLOAD' | 'UPLOADED' | 'DELETED';
};

export type JobError = { code: string; message: string; stage: string };

export type AnalysisStatus = {
  id: string;
  upload_id: string;
  status: JobStatus;
  current_stage: string;
  error: JobError | null;
  created_at: string;
  completed_at: string | null;
};

export type ActionItem = { description: string; owner: string | null };

export type StandardOutput = {
  language: string | null;
  summary: string;
  topics: string[];
  sentiment: 'positive' | 'neutral' | 'negative' | 'mixed' | 'unknown';
  speakers_count: number | null;
  action_items: ActionItem[];
  key_points: string[];
};

export type Provenance = {
  provider: string;
  model: string;
  prompt_version: string;
  latency_ms: number;
};

export type AnalysisSection<T> = {
  analysis_type: string;
  summary: string;
  structured_output: T;
  template: { slug: string; name: string; version: number } | null;
  provenance: Provenance;
};

export type AnalysisResult = AnalysisStatus & {
  transcript: {
    text: string;
    language: string;
    duration_seconds: number | null;
    provider: string;
    model: string;
  };
  standard_analysis: AnalysisSection<StandardOutput>;
  // Template output: `summary` plus named lists of strings declared by the template.
  custom_analysis: AnalysisSection<Record<string, string | string[]>> | null;
};

export type ApiErrorBody = { error: { code: string; message: string } };
