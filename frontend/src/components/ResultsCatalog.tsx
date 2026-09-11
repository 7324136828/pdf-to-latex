import React, { useState } from 'react';
import {
  Download,
  Eye,
  Archive,
  BookOpen,
  CheckCircle2,
} from 'lucide-react';
import {
  OutputFile,
  getDownloadZipUrl,
  getFileContent,
  getFileDownloadUrl,
} from '../services/api';
import { TeXPreviewModal } from './TeXPreviewModal';

interface ResultsCatalogProps {
  jobId: string;
  outputs: OutputFile[];
}

export const ResultsCatalog: React.FC<ResultsCatalogProps> = ({ jobId, outputs }) => {
  const [previewFile, setPreviewFile] = useState<string | null>(null);
  const [previewContent, setPreviewContent] = useState<string>('');
  const [loadingPreview, setLoadingPreview] = useState(false);

  const handlePreview = async (filename: string) => {
    setLoadingPreview(true);
    setPreviewFile(filename);
    try {
      const text = await getFileContent(jobId, filename);
      setPreviewContent(text);
    } catch (err) {
      setPreviewContent('% Error loading file content.');
    } finally {
      setLoadingPreview(false);
    }
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  };

  return (
    <div className="card" style={{ marginTop: '2rem' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '1rem',
          marginBottom: '1.25rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: '50%',
              background: 'var(--success-bg)',
              color: 'var(--success)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <CheckCircle2 size={20} />
          </div>
          <div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }}>
              Generated LaTeX Output ({outputs.length} files)
            </h2>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
              Split per chapter according to document bookmarks and headings
            </p>
          </div>
        </div>

        <a
          href={getDownloadZipUrl(jobId)}
          className="btn btn-primary"
          style={{ textDecoration: 'none' }}
          download
        >
          <Archive size={16} /> Download All (ZIP)
        </a>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table className="results-table">
          <thead>
            <tr>
              <th>Chapter / Section</th>
              <th>Filename</th>
              <th>Converter</th>
              <th>Size</th>
              <th style={{ textAlign: 'right' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {outputs.map((out) => (
              <tr key={out.filename}>
                <td>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 500 }}>
                    <BookOpen size={16} color="#818cf8" />
                    <span>{out.chapter_title}</span>
                  </div>
                </td>
                <td>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                    {out.filename}
                  </span>
                </td>
                <td>
                  <span
                    style={{
                      fontSize: '0.75rem',
                      padding: '0.2rem 0.5rem',
                      borderRadius: 'var(--radius-sm)',
                      background:
                        out.converter === 'pdf2tex'
                          ? 'rgba(168, 85, 247, 0.15)'
                          : 'rgba(99, 102, 241, 0.15)',
                      color: out.converter === 'pdf2tex' ? '#c084fc' : '#a5b4fc',
                      fontWeight: 600,
                      textTransform: 'uppercase',
                    }}
                  >
                    {out.converter}
                  </span>
                </td>
                <td style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                  {formatBytes(out.size_bytes)}
                </td>
                <td style={{ textAlign: 'right' }}>
                  <div style={{ display: 'inline-flex', gap: '0.5rem' }}>
                    <button
                      className="btn btn-secondary"
                      style={{ padding: '0.4rem 0.65rem', fontSize: '0.8rem' }}
                      onClick={() => handlePreview(out.filename)}
                      title="Preview LaTeX"
                    >
                      <Eye size={14} /> Preview
                    </button>
                    <a
                      href={getFileDownloadUrl(jobId, out.filename)}
                      className="btn btn-secondary"
                      style={{ padding: '0.4rem 0.65rem', fontSize: '0.8rem', textDecoration: 'none' }}
                      download={out.filename.split('/').pop()}
                      title="Download .tex"
                    >
                      <Download size={14} /> Download
                    </a>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {previewFile && (
        <TeXPreviewModal
          filename={previewFile}
          content={loadingPreview ? 'Loading file preview...' : previewContent}
          onClose={() => setPreviewFile(null)}
        />
      )}
    </div>
  );
};
