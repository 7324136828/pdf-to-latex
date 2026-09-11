import React from 'react';
import {
  Archive,
  ArrowRight,
  CheckCircle2,
  CirclePause,
  Clock3,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Trash2,
  XCircle,
} from 'lucide-react';
import { JobSummaryResponse, getDownloadZipUrl } from '../services/api';

interface HistoryViewProps {
  jobs: JobSummaryResponse[];
  loading: boolean;
  error: string | null;
  actionJobId: string | null;
  onContinue: (job: JobSummaryResponse) => void;
  onDiscard: (job: JobSummaryResponse) => void;
  onRefresh: () => void;
}

const statusDetails = (status: JobSummaryResponse['status']) => {
  switch (status) {
    case 'completed':
      return { label: 'Completed', className: 'completed', icon: <CheckCircle2 size={15} /> };
    case 'failed':
      return { label: 'Failed', className: 'failed', icon: <XCircle size={15} /> };
    case 'interrupted':
      return { label: 'Interrupted', className: 'interrupted', icon: <CirclePause size={15} /> };
    case 'discarding':
      return { label: 'Discarding', className: 'running', icon: <Loader2 className="animate-spin" size={15} /> };
    case 'queued':
      return { label: 'Queued', className: 'running', icon: <Clock3 size={15} /> };
    default:
      return { label: 'In progress', className: 'running', icon: <Loader2 className="animate-spin" size={15} /> };
  }
};

const formatDate = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));

export const HistoryView: React.FC<HistoryViewProps> = ({
  jobs,
  loading,
  error,
  actionJobId,
  onContinue,
  onDiscard,
  onRefresh,
}) => (
  <section className="history-screen" aria-labelledby="history-title">
    <div className="history-heading">
      <div>
        <div className="history-eyebrow"><Archive size={15} /> Saved conversions</div>
        <h2 id="history-title">Conversion history</h2>
        <p>Continue active work or download completed LaTeX bundles.</p>
      </div>
      <button className="btn btn-secondary" onClick={onRefresh} disabled={loading}>
        <RefreshCw className={loading ? 'animate-spin' : ''} size={16} /> Refresh
      </button>
    </div>

    {error && <div className="history-error" role="alert">{error}</div>}

    {loading && jobs.length === 0 ? (
      <div className="history-empty"><Loader2 className="animate-spin" size={28} /> Loading conversions…</div>
    ) : jobs.length === 0 ? (
      <div className="history-empty">
        <Archive size={32} />
        <strong>No conversions yet</strong>
        <span>Conversions you start will appear here automatically.</span>
      </div>
    ) : (
      <div className="history-list">
        {jobs.map((job) => {
          const status = statusDetails(job.status);
          const isWorking = actionJobId === job.job_id;
          const canDiscard = job.status !== 'completed' && job.status !== 'discarding';
          return (
            <article className="history-item" key={job.job_id}>
              <div className="history-item-main">
                <div className="history-item-topline">
                  <span className={`history-status ${status.className}`}>{status.icon}{status.label}</span>
                  <span className="history-date">{formatDate(job.created_at)}</span>
                </div>
                <div className="history-files">
                  <FileText size={18} />
                  <div>
                    <strong>{job.source_files[0] || `${job.files_count} PDF files`}</strong>
                    {job.source_files.length > 1 && (
                      <span>{job.source_files.slice(1, 3).join(', ')}{job.source_files.length > 3 ? ` +${job.source_files.length - 3} more` : ''}</span>
                    )}
                  </div>
                </div>
                <p className="history-message">{job.message}</p>
                <div className="history-metadata">
                  <span>{job.files_count} PDF{job.files_count === 1 ? '' : 's'}</span>
                  {job.status === 'completed' && <span>{job.chapters_count} LaTeX file{job.chapters_count === 1 ? '' : 's'}</span>}
                  <span className="history-job-id">{job.job_id.slice(0, 8)}</span>
                </div>
              </div>

              <div className="history-actions">
                {job.status === 'completed' ? (
                  <a className="btn btn-primary" href={getDownloadZipUrl(job.job_id)} download>
                    <Download size={16} /> Download ZIP
                  </a>
                ) : job.status === 'discarding' ? (
                  <span className="history-discarding"><Loader2 className="animate-spin" size={16} /> Discarding…</span>
                ) : (
                  <button className="btn btn-primary" onClick={() => onContinue(job)} disabled={isWorking}>
                    {isWorking ? 'Opening…' : job.status === 'queued' || job.status === 'converting' ? 'Continue' : 'Resume'}
                    <ArrowRight size={16} />
                  </button>
                )}
                {canDiscard && (
                  <button className="btn btn-danger-ghost" onClick={() => onDiscard(job)} disabled={isWorking}>
                    <Trash2 size={16} /> Discard
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>
    )}
  </section>
);
