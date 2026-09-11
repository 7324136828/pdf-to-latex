import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Upload, FileText, Trash2, Clipboard, Plus } from 'lucide-react';

interface FileUploadZoneProps {
  files: File[];
  onFilesChange: (files: File[]) => void;
  disabled?: boolean;
}

export const FileUploadZone: React.FC<FileUploadZoneProps> = ({
  files,
  onFilesChange,
  disabled = false,
}) => {
  const [isDragging, setIsDragging] = useState(false);
  const [pasteNotification, setPasteNotification] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback(
    (newFiles: FileList | File[]) => {
      const valid = Array.from(newFiles).filter((file) =>
        file.name.toLowerCase().endsWith('.pdf') || file.type === 'application/pdf'
      );
      if (valid.length === 0) return;

      // Avoid exact duplicates by name and size
      const existingKeys = new Set(files.map((f) => `${f.name}-${f.size}`));
      const nonDuplicates = valid.filter((f) => !existingKeys.has(`${f.name}-${f.size}`));

      if (nonDuplicates.length > 0) {
        onFilesChange([...files, ...nonDuplicates]);
      }
    },
    [files, onFilesChange]
  );

  // Global paste handler (Ctrl+V anywhere on window)
  useEffect(() => {
    const handlePaste = (e: ClipboardEvent) => {
      if (disabled) return;
      if (!e.clipboardData) return;

      const clipboardFiles = e.clipboardData.files;
      if (clipboardFiles && clipboardFiles.length > 0) {
        const pdfs = Array.from(clipboardFiles).filter(
          (f) => f.name.toLowerCase().endsWith('.pdf') || f.type === 'application/pdf'
        );
        if (pdfs.length > 0) {
          e.preventDefault();
          addFiles(pdfs);
          setPasteNotification(`Pasted ${pdfs.length} PDF file(s) from clipboard!`);
          setTimeout(() => setPasteNotification(null), 3500);
        }
      }
    };

    window.addEventListener('paste', handlePaste);
    return () => window.removeEventListener('paste', handlePaste);
  }, [addFiles, disabled]);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (!disabled) setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (disabled) return;
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      addFiles(e.dataTransfer.files);
    }
  };

  const removeFile = (index: number) => {
    const next = [...files];
    next.splice(index, 1);
    onFilesChange(next);
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  };

  return (
    <div>
      <div
        className={`dropzone ${isDragging ? 'active' : ''}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => !disabled && fileInputRef.current?.click()}
        role="button"
        tabIndex={0}
        aria-label="Upload PDF files by selecting, dropping, or pasting"
      >
        <input
          type="file"
          ref={fileInputRef}
          style={{ display: 'none' }}
          accept=".pdf,application/pdf"
          multiple
          disabled={disabled}
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = '';
          }}
        />

        <div className="dropzone-icon">
          <Upload size={28} />
        </div>
        <div className="dropzone-title">Select, Drop, or Paste PDF Files</div>
        <div className="dropzone-hint">
          Click to browse or drag and drop textbooks, papers, and lecture notes
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center', flexWrap: 'wrap' }}>
          <span className="paste-badge">
            <Clipboard size={14} /> Press Ctrl+V to paste copied PDF
          </span>
          <span className="paste-badge">
            <Plus size={14} /> Batch files supported
          </span>
        </div>
      </div>

      {pasteNotification && (
        <div
          style={{
            marginTop: '0.75rem',
            padding: '0.65rem 1rem',
            background: 'var(--accent-light)',
            color: '#a5b4fc',
            borderRadius: 'var(--radius-sm)',
            fontSize: '0.9rem',
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
            border: '1px solid rgba(99, 102, 241, 0.3)',
          }}
        >
          <Clipboard size={16} /> {pasteNotification}
        </div>
      )}

      {files.length > 0 && (
        <div className="file-list">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', fontWeight: 500 }}>
              Selected Files ({files.length})
            </span>
            <button
              className="btn-remove"
              onClick={() => onFilesChange([])}
              disabled={disabled}
              title="Clear all files"
              style={{ fontSize: '0.85rem' }}
            >
              Clear All
            </button>
          </div>

          {files.map((file, idx) => (
            <div key={`${file.name}-${idx}`} className="file-item">
              <div className="file-info">
                <FileText className="file-icon" size={20} />
                <div>
                  <div className="file-name">{file.name}</div>
                  <div className="file-size">{formatBytes(file.size)}</div>
                </div>
              </div>
              <button
                className="btn-remove"
                onClick={(e) => {
                  e.stopPropagation();
                  removeFile(idx);
                }}
                disabled={disabled}
                title="Remove file"
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
