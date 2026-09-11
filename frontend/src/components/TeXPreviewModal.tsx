import React, { useState } from 'react';
import { X, Copy, Check, FileCode } from 'lucide-react';

interface TeXPreviewModalProps {
  filename: string;
  content: string;
  onClose: () => void;
}

export const TeXPreviewModal: React.FC<TeXPreviewModalProps> = ({
  filename,
  content,
  onClose,
}) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy text', err);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
            <FileCode size={20} color="#818cf8" />
            <h3 style={{ fontSize: '1.1rem', fontWeight: 600 }}>{filename}</h3>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <button
              className="btn btn-secondary"
              onClick={handleCopy}
              style={{ padding: '0.45rem 0.85rem', fontSize: '0.85rem' }}
            >
              {copied ? (
                <>
                  <Check size={14} color="var(--success)" /> Copied!
                </>
              ) : (
                <>
                  <Copy size={14} /> Copy LaTeX
                </>
              )}
            </button>
            <button
              onClick={onClose}
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                padding: '0.35rem',
              }}
              title="Close modal"
            >
              <X size={20} />
            </button>
          </div>
        </div>

        <div className="modal-body">
          <pre className="code-viewer">
            <code>{content}</code>
          </pre>
        </div>
      </div>
    </div>
  );
};
