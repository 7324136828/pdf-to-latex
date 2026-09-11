import React, { useCallback, useEffect, useState } from 'react';
import {
  FileCode2,
  Cpu,
  Sparkles,
  ArrowRight,
  RotateCcw,
  History,
  UploadCloud,
} from 'lucide-react';
import { FileUploadZone } from './components/FileUploadZone';
import { OptionsPanel } from './components/OptionsPanel';
import { ProgressView } from './components/ProgressView';
import { ResultsCatalog } from './components/ResultsCatalog';
import { HistoryView } from './components/HistoryView';
import {
  ConversionOptions,
  HealthResponse,
  JobSummaryResponse,
  JobStatusResponse,
  checkHealth,
  deleteJob,
  getJobStatus,
  listJobs,
  resumeJob,
  submitConversion,
} from './services/api';

export const App: React.FC = () => {
  const [screen, setScreen] = useState<'convert' | 'history'>(
    window.location.hash === '#/history' ? 'history' : 'convert'
  );
  const [files, setFiles] = useState<File[]>([]);
  const [options, setOptions] = useState<ConversionOptions>({
    device: 'auto',
    mode: 'hybrid',
    dpi: 300,
    min_section_chars: 3000,
    force: false,
  });
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatusResponse | null>(null);
  const [recoverableJobs, setRecoverableJobs] = useState<JobSummaryResponse[]>([]);
  const [recoveringJobId, setRecoveringJobId] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const upsertRecoverableJob = useCallback((job: JobSummaryResponse) => {
    setRecoverableJobs((current) => {
      const withoutJob = current.filter((item) => item.job_id !== job.job_id);
      return [job, ...withoutJob].sort((a, b) => b.created_at.localeCompare(a.created_at));
    });
  }, []);

  const refreshJobs = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      setRecoverableJobs(await listJobs());
    } catch (err: any) {
      setHistoryError(err.message || 'Could not load conversion history');
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const navigate = (nextScreen: 'convert' | 'history') => {
    window.location.hash = nextScreen === 'history' ? '#/history' : '#/convert';
    setScreen(nextScreen);
  };

  // Poll health and discover conversions retained by the backend on mount.
  useEffect(() => {
    checkHealth()
      .then(setHealth)
      .catch((err) => console.warn('Backend not yet reachable:', err.message));
    refreshJobs();

    const handleHashChange = () => {
      setScreen(window.location.hash === '#/history' ? 'history' : 'convert');
    };
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, [refreshJobs]);

  useEffect(() => {
    if (screen !== 'history') return;
    refreshJobs();
    const interval = window.setInterval(refreshJobs, 2500);
    return () => window.clearInterval(interval);
  }, [screen, refreshJobs]);

  // Poll active job status
  useEffect(() => {
    if (!activeJobId) return;

    let isMounted = true;
    const pollInterval = setInterval(async () => {
      try {
        const status = await getJobStatus(activeJobId);
        if (!isMounted) return;
        setJobStatus(status);
        upsertRecoverableJob(status);

        if (
          status.status === 'completed' ||
          status.status === 'failed' ||
          status.status === 'interrupted'
        ) {
          clearInterval(pollInterval);
        }
      } catch (err) {
        console.error('Error polling status:', err);
      }
    }, 1200);

    return () => {
      isMounted = false;
      clearInterval(pollInterval);
    };
  }, [activeJobId, upsertRecoverableJob]);

  const handleStartConversion = async () => {
    if (files.length === 0) return;
    setSubmitting(true);
    setSubmitError(null);

    try {
      const res = await submitConversion(files, options);
      setActiveJobId(res.job_id);
      // Immediately fetch initial status
      const initialStatus = await getJobStatus(res.job_id);
      setJobStatus(initialStatus);
      upsertRecoverableJob(initialStatus);
    } catch (err: any) {
      setSubmitError(err.message || 'Conversion request failed');
    } finally {
      setSubmitting(false);
    }
  };

  const handleRecoverJob = async (job: JobSummaryResponse) => {
    setRecoveringJobId(job.job_id);
    setSubmitError(null);
    try {
      const current =
        job.status === 'interrupted' || job.status === 'failed'
          ? await resumeJob(job.job_id)
          : await getJobStatus(job.job_id);
      setActiveJobId(current.job_id);
      setJobStatus(current);
      upsertRecoverableJob(current);
      navigate('convert');
    } catch (err: any) {
      setSubmitError(err.message || 'Could not recover the conversion');
    } finally {
      setRecoveringJobId(null);
    }
  };

  const handleDiscardJob = async (job: JobSummaryResponse) => {
    const filenames = job.source_files.length > 0 ? job.source_files.join(', ') : 'this conversion';
    if (!window.confirm(`Discard ${filenames}? Saved checkpoints and generated files will be removed.`)) {
      return;
    }
    setRecoveringJobId(job.job_id);
    setHistoryError(null);
    try {
      await deleteJob(job.job_id);
      setRecoverableJobs((current) => current.filter((item) => item.job_id !== job.job_id));
      if (activeJobId === job.job_id) {
        setActiveJobId(null);
        setJobStatus(null);
      }
    } catch (err: any) {
      setHistoryError(err.message || 'Could not discard the conversion');
    } finally {
      setRecoveringJobId(null);
    }
  };

  const handleReset = () => {
    setActiveJobId(null);
    setJobStatus(null);
    setFiles([]);
    setSubmitError(null);
  };

  const recoveryJob = recoverableJobs.find((job) => job.job_id !== activeJobId);
  const recoveryAction = recoveryJob?.status === 'completed' ? 'View results' :
    recoveryJob?.status === 'queued' || recoveryJob?.status === 'converting' ? 'View progress' :
    'Resume conversion';

  return (
    <div className="container">
      {screen === 'convert' && recoveryJob && (
        <div className="recovery-toast" role="status" aria-live="polite">
          <div className="recovery-toast-icon">
            <History size={20} />
          </div>
          <div className="recovery-toast-copy">
            <strong>
              {recoveryJob.status === 'completed'
                ? 'Previous conversion is ready'
                : recoveryJob.status === 'converting' || recoveryJob.status === 'queued'
                ? 'Conversion is still running'
                : 'Interrupted conversion found'}
            </strong>
            <span>
              {recoveryJob.files_count} PDF{recoveryJob.files_count === 1 ? '' : 's'}
              {recoverableJobs.length > 1 ? ` · ${recoverableJobs.length - 1} more saved` : ''}
            </span>
          </div>
          <button
            className="btn btn-primary recovery-toast-action"
            onClick={() => handleRecoverJob(recoveryJob)}
            disabled={recoveringJobId === recoveryJob.job_id}
          >
            {recoveringJobId === recoveryJob.job_id ? 'Opening…' : recoveryAction}
            <ArrowRight size={16} />
          </button>
        </div>
      )}
      <header>
        <div className="header-badge">
          <Sparkles size={14} />
          <span>Formula-Aware OCR & Chapter Splitting</span>
        </div>
        <h1>PDF → LaTeX</h1>
        <p className="subtitle">
          Transform mathematical and scientific PDFs into structured, compilable LaTeX
          chapters with exact text recovery and Pix2Text GPU OCR.
        </p>

        {health && (
          <div style={{ marginTop: '1rem', display: 'inline-flex', gap: '0.75rem', fontSize: '0.85rem' }}>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                background: 'rgba(255, 255, 255, 0.05)',
                padding: '0.25rem 0.65rem',
                borderRadius: 'var(--radius-sm)',
                color: health.has_rtx ? 'var(--success)' : 'var(--text-secondary)',
                border: '1px solid var(--border-color)',
              }}
            >
              <Cpu size={14} />
              {health.has_rtx
                ? `GPU Active (${health.gpu_names[0] || 'RTX'})`
                : health.gpu_available
                ? `GPU (${health.gpu_names[0]})`
                : 'CPU Mode'}
            </span>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                background: 'rgba(255, 255, 255, 0.05)',
                padding: '0.25rem 0.65rem',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border-color)',
              }}
            >
              <FileCode2 size={14} /> Python {health.python_version}
            </span>
          </div>
        )}
      </header>

      <nav className="app-nav" aria-label="Application sections">
        <button
          className={screen === 'convert' ? 'active' : ''}
          onClick={() => navigate('convert')}
          aria-current={screen === 'convert' ? 'page' : undefined}
        >
          <UploadCloud size={17} /> Convert PDFs
        </button>
        <button
          className={screen === 'history' ? 'active' : ''}
          onClick={() => navigate('history')}
          aria-current={screen === 'history' ? 'page' : undefined}
        >
          <History size={17} /> Conversion history
          {recoverableJobs.length > 0 && <span className="nav-count">{recoverableJobs.length}</span>}
        </button>
      </nav>

      <main>
        {screen === 'history' ? (
          <HistoryView
            jobs={recoverableJobs}
            loading={historyLoading}
            error={historyError}
            actionJobId={recoveringJobId}
            onContinue={handleRecoverJob}
            onDiscard={handleDiscardJob}
            onRefresh={refreshJobs}
          />
        ) : !jobStatus ? (
          <div className="card">
            <FileUploadZone
              files={files}
              onFilesChange={setFiles}
              disabled={submitting}
            />

            <OptionsPanel
              options={options}
              onChange={setOptions}
              disabled={submitting}
            />

            {submitError && (
              <div
                style={{
                  background: 'var(--danger-bg)',
                  color: '#fca5a5',
                  padding: '0.85rem 1rem',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid rgba(239, 68, 68, 0.3)',
                  marginTop: '1.25rem',
                  fontSize: '0.9rem',
                }}
              >
                {submitError}
              </div>
            )}

            <div style={{ marginTop: '1.75rem', display: 'flex', justifyContent: 'flex-end' }}>
              <button
                className="btn btn-primary"
                onClick={handleStartConversion}
                disabled={files.length === 0 || submitting}
              >
                {submitting ? (
                  'Preparing Upload...'
                ) : (
                  <>
                    Convert {files.length > 0 ? `${files.length} PDF(s)` : ''} to LaTeX
                    <ArrowRight size={18} />
                  </>
                )}
              </button>
            </div>
          </div>
        ) : (
          <div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '1rem' }}>
              <button
                className="btn btn-secondary"
                onClick={handleReset}
                style={{ fontSize: '0.85rem' }}
              >
                <RotateCcw size={14} /> Convert Another Document
              </button>
            </div>

            <ProgressView job={jobStatus} />

            {jobStatus.status === 'completed' && jobStatus.outputs.length > 0 && (
              <ResultsCatalog jobId={jobStatus.job_id} outputs={jobStatus.outputs} />
            )}
          </div>
        )}
      </main>
    </div>
  );
};

export default App;
