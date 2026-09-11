import React from 'react';
import { Settings, Cpu, Layers } from 'lucide-react';
import { ConversionOptions } from '../services/api';

interface OptionsPanelProps {
  options: ConversionOptions;
  onChange: (options: ConversionOptions) => void;
  disabled?: boolean;
}

export const OptionsPanel: React.FC<OptionsPanelProps> = ({
  options,
  onChange,
  disabled = false,
}) => {
  const updateOption = <K extends keyof ConversionOptions>(
    key: K,
    value: ConversionOptions[K]
  ) => {
    onChange({ ...options, [key]: value });
  };

  return (
    <div className="card" style={{ marginTop: '1.5rem', padding: '1.25rem 1.75rem' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          fontSize: '1rem',
          fontWeight: 600,
          color: 'var(--text-primary)',
          marginBottom: '1rem',
        }}
      >
        <Settings size={18} color="#818cf8" />
        <span>Conversion Configuration</span>
      </div>

      <div className="options-grid">
        <div className="option-group">
          <label className="option-label" htmlFor="opt-device">
            <Cpu size={14} style={{ display: 'inline', marginRight: 4, verticalAlign: -2 }} />
            Execution Device
          </label>
          <select
            id="opt-device"
            className="option-select"
            value={options.device}
            disabled={disabled}
            onChange={(e) => updateOption('device', e.target.value as ConversionOptions['device'])}
          >
            <option value="auto">Auto (RTX GPU if detected)</option>
            <option value="cuda">CUDA (Force GPU)</option>
            <option value="cpu">CPU (Standard)</option>
          </select>
        </div>

        <div className="option-group">
          <label className="option-label" htmlFor="opt-mode">
            <Layers size={14} style={{ display: 'inline', marginRight: 4, verticalAlign: -2 }} />
            Converter Mode
          </label>
          <select
            id="opt-mode"
            className="option-select"
            value={options.mode}
            disabled={disabled}
            onChange={(e) => updateOption('mode', e.target.value as ConversionOptions['mode'])}
          >
            <option value="hybrid">Hybrid (Text layer + OCR fallback)</option>
            <option value="traditional_only">Traditional Only (Fast exact text)</option>
            <option value="pdf2tex_only">OCR Only (Formula-aware Pix2Text)</option>
          </select>
        </div>

        <div className="option-group">
          <label className="option-label" htmlFor="opt-dpi">
            OCR Resolution (DPI)
          </label>
          <select
            id="opt-dpi"
            className="option-select"
            value={options.dpi}
            disabled={disabled}
            onChange={(e) => updateOption('dpi', parseInt(e.target.value, 10))}
          >
            <option value="150">150 DPI (Fast preview)</option>
            <option value="200">200 DPI (Balanced)</option>
            <option value="300">300 DPI (High precision, recommended)</option>
            <option value="400">400 DPI (Ultra detailed formulas)</option>
          </select>
        </div>

        <div className="option-group">
          <label className="option-label" htmlFor="opt-min-chars">
            Min Chapter Chars
          </label>
          <input
            id="opt-min-chars"
            type="number"
            className="option-input"
            value={options.min_section_chars}
            disabled={disabled}
            min={0}
            step={500}
            onChange={(e) => updateOption('min_section_chars', parseInt(e.target.value, 10) || 0)}
          />
        </div>
      </div>
    </div>
  );
};
