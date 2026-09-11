import React, { useEffect, useRef } from 'react';
import { Loader2, CheckCircle2, XCircle, Terminal, CirclePause } from 'lucide-react';
import { JobStatusResponse } from '../services/api';

interface ProgressViewProps {
  job: JobStatusResponse;
}

export const ProgressView: React.FC<ProgressViewProps> = ({ job }) => {
  const terminalEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [job.logs]);

  const getStatusBadge = () => {
    switch (job.status) {
      case 'queued':
        return (
          <span style={{ color: 'var(--warning)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Loader2 className="animate-spin" size={16} /> Queued
          </span>
        );
      case 'converting':
        return (
          <span style={{ color: '#818cf8', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Loader2 className="animate-spin" size={16} /> Converting...
          </span>
        );
      case 'interrupted':
        return (
          <span style={{ color: 'var(--warning)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <CirclePause size={16} /> Interrupted
          </span>
        );
      case 'completed':
        return (
          <span style={{ color: 'var(--success)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <CheckCircle2 size={16} /> Conversion Finished
          </span>
        );
      case 'failed':
        return (
          <span style={{ color: 'var(--danger)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <XCircle size={16} /> Failed
          </span>
        );
      default:
        return null;
    }
  };

  return (
    <div className="card" style={{ marginTop: '1.5rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: '1.1rem' }}>{job.message}</div>
          <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Job ID: {job.job_id}</div>
        </div>
        <div>{getStatusBadge()}</div>
      </div>

      <div className="progress-bar-container">
        <div
          className="progress-bar"
          style={{
            width: `${Math.max(5, Math.min(100, job.progress * 100))}%`,
            background:
              job.status === 'failed'
                ? 'var(--danger)'
                : job.status === 'completed'
                ? 'var(--success)'
                : undefined,
          }}
        />
      </div>

      {job.error && (
        <div
          style={{
            background: 'var(--danger-bg)',
            color: '#fca5a5',
            padding: '0.85rem 1rem',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            marginBottom: '1rem',
            fontSize: '0.9rem',
          }}
        >
          <strong>Error:</strong> {job.error}
        </div>
      )}

      <div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontSize: '0.85rem',
            color: 'var(--text-secondary)',
            marginBottom: '0.5rem',
          }}
        >
          <Terminal size={14} /> Conversion Output & Diagnostics
        </div>
        <div className="log-terminal">
          {job.logs.length === 0 ? (
            <div style={{ color: 'var(--text-muted)' }}>Waiting for log output...</div>
          ) : (
            job.logs.map((log, i) => (
              <div
                key={i}
                className={`log-line ${
                  log.includes('ERROR') || log.includes('FAILED')
                    ? 'error'
                    : log.includes('SUCCESS') || log.includes('READY')
                    ? 'success'
                    : ''
                }`}
              >
                {log}
              </div>
            ))
          )}
          <div ref={terminalEndRef} />
        </div>
      </div>
    </div>
  );
};
