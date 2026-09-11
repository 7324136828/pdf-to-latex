export interface OutputFile {
  filename: string;
  chapter_title: string;
  size_bytes: number;
  sha256: string;
  converter: string;
}

export interface ConversionOptions {
  device: 'auto' | 'cuda' | 'cpu';
  mode: 'hybrid' | 'traditional_only' | 'pdf2tex_only';
  dpi: number;
  min_section_chars: number;
  force: boolean;
}

export interface JobStatusResponse {
  job_id: string;
  status: 'queued' | 'converting' | 'interrupted' | 'discarding' | 'completed' | 'failed';
  progress: number;
  message: string;
  files_count: number;
  source_files: string[];
  chapters_count: number;
  logs: string[];
  outputs: OutputFile[];
  created_at: string;
  completed_at?: string;
  error?: string;
}

export type JobSummaryResponse = Pick<
  JobStatusResponse,
  | 'job_id'
  | 'status'
  | 'message'
  | 'files_count'
  | 'source_files'
  | 'chapters_count'
  | 'created_at'
  | 'completed_at'
>;

export interface HealthResponse {
  status: string;
  gpu_available: boolean;
  gpu_names: string[];
  has_rtx: boolean;
  python_version: string;
}

const API_BASE = '/api';
const BACKEND_UNAVAILABLE_MESSAGE =
  'Cannot reach the PDF conversion service. Start the application with run.bat (Windows) or ./run.sh (Linux/macOS), then try again.';

async function requestApi(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE}${path}`, init);
  } catch (error) {
    // Vite reports an unavailable proxy/backend as the unhelpful browser-level
    // "Failed to fetch" error.  Preserve other errors but make this common
    // local setup problem actionable.
    if (error instanceof TypeError) {
      throw new Error(BACKEND_UNAVAILABLE_MESSAGE);
    }
    throw error;
  }
}

export async function checkHealth(): Promise<HealthResponse> {
  const res = await requestApi('/health');
  if (!res.ok) throw new Error('Health check failed');
  return res.json();
}

export async function submitConversion(
  files: File[],
  options: ConversionOptions
): Promise<{ job_id: string; status: string; message: string }> {
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file);
  }
  formData.append('device', options.device);
  formData.append('mode', options.mode);
  formData.append('dpi', options.dpi.toString());
  formData.append('min_section_chars', options.min_section_chars.toString());
  formData.append('force', options.force.toString());

  const res = await requestApi('/convert', {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(errorData.detail || 'Failed to submit files for conversion');
  }

  return res.json();
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const res = await requestApi(`/jobs/${jobId}`);
  if (!res.ok) throw new Error(`Failed to fetch status for job ${jobId}`);
  return res.json();
}

export async function listJobs(): Promise<JobSummaryResponse[]> {
  const res = await requestApi('/jobs');
  if (!res.ok) throw new Error('Failed to retrieve previous conversions');
  const jobs = (await res.json()) as JobSummaryResponse[];
  return jobs.map((job) => ({ ...job, source_files: job.source_files ?? [] }));
}

export async function resumeJob(jobId: string): Promise<JobStatusResponse> {
  const res = await requestApi(`/jobs/${jobId}/resume`, { method: 'POST' });
  if (!res.ok) throw new Error(`Failed to resume job ${jobId}`);
  return res.json();
}

export async function deleteJob(jobId: string): Promise<void> {
  const res = await requestApi(`/jobs/${jobId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Failed to discard job ${jobId}`);
}

export async function getFileContent(jobId: string, filename: string): Promise<string> {
  const res = await requestApi(`/jobs/${jobId}/files/${encodeURIComponent(filename)}`);
  if (!res.ok) throw new Error(`Failed to read file ${filename}`);
  return res.text();
}

export function getFileDownloadUrl(jobId: string, filename: string): string {
  return `${API_BASE}/jobs/${jobId}/files/${encodeURIComponent(filename)}?download=true`;
}

export function getDownloadZipUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/download-zip`;
}
