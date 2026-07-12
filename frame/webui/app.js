function updateRangeOutputs(root = document) {
  root.querySelectorAll('input[type="range"][data-value-for]').forEach((input) => {
    const output = root.querySelector(`[data-output-for="${input.dataset.valueFor}"]`);
    if (output) {
      output.textContent = input.value;
    }
  });
}

function setValue(name, value) {
  const field = document.querySelector(`[name="${name}"]`);
  if (!field) return;
  if (field.type === 'checkbox') {
    field.checked = Boolean(value);
  } else {
    field.value = value;
  }
  field.dispatchEvent(new Event('input', { bubbles: true }));
  field.dispatchEvent(new Event('change', { bubbles: true }));
}

function updateModeSections() {
  const modeField = document.querySelector('[name="content_mode"]');
  const mode = modeField ? modeField.value : 'birds';
  const paintingsMode = mode === 'paintings' || mode === 'vangogh';
  document.querySelectorAll('.paintings-only').forEach((el) => {
    el.style.display = paintingsMode ? '' : 'none';
  });
  document.querySelectorAll('.birds-only').forEach((el) => {
    el.style.display = paintingsMode ? 'none' : '';
  });
}

function updatePaintingSelection() {
  document.querySelectorAll('.painting-card').forEach((card) => {
    const input = card.querySelector('input[type="radio"]');
    if (!input) return;
    card.classList.toggle('selected', input.checked);
  });
}

function getEditorElements() {
  return {
    image: document.querySelector('#painting-live-image'),
    keyInput: document.querySelector('#editor_painting_key'),
    deleteKeyInput: document.querySelector('#delete_painting_key'),
    saveScale: document.querySelector('#editor_save_scale'),
    saveX: document.querySelector('#editor_save_offset_x'),
    saveY: document.querySelector('#editor_save_offset_y'),
    scale: document.querySelector('[name="editor_scale"]'),
    offsetX: document.querySelector('[name="editor_offset_x"]'),
    offsetY: document.querySelector('[name="editor_offset_y"]'),
    deleteForm: document.querySelector('form[action="/delete-painting"]'),
  };
}

function applyEditorPreview() {
  const els = getEditorElements();
  if (!els.image || !els.scale || !els.offsetX || !els.offsetY) return;
  const scale = Number(els.scale.value || 1);
  const offsetX = Number(els.offsetX.value || 0);
  const offsetY = Number(els.offsetY.value || 0);

  // Always keep form save fields in sync, even if preview cannot be drawn yet.
  if (els.saveScale) els.saveScale.value = String(scale);
  if (els.saveX) els.saveX.value = String(offsetX);
  if (els.saveY) els.saveY.value = String(offsetY);

  const wrap = els.image.parentElement;
  if (!wrap) return;

  const naturalW = els.image.naturalWidth || 0;
  const naturalH = els.image.naturalHeight || 0;
  const wrapW = wrap.clientWidth || 0;
  const wrapH = wrap.clientHeight || 0;
  if (naturalW <= 0 || naturalH <= 0 || wrapW <= 0 || wrapH <= 0) return;

  // Mirror the renderer's cover + crop behavior so preview == saved output.
  const cover = Math.max(wrapW / naturalW, wrapH / naturalH);
  const drawW = Math.max(1, Math.round(naturalW * cover * scale));
  const drawH = Math.max(1, Math.round(naturalH * cover * scale));
  const maxX = Math.max(0, drawW - wrapW);
  const maxY = Math.max(0, drawH - wrapH);
  const normX = Math.max(-100, Math.min(100, offsetX)) / 100;
  const normY = Math.max(-100, Math.min(100, offsetY)) / 100;
  const cropX = Math.round(maxX / 2 + normX * (maxX / 2));
  const cropY = Math.round(maxY / 2 + normY * (maxY / 2));

  els.image.style.width = `${drawW}px`;
  els.image.style.height = `${drawH}px`;
  els.image.style.maxWidth = 'none';
  els.image.style.maxHeight = 'none';
  els.image.style.left = `${-cropX}px`;
  els.image.style.top = `${-cropY}px`;
  els.image.style.transform = 'none';
}

function loadEditorFromSelectedCard() {
  const selected = document.querySelector('.painting-card input[type="radio"]:checked');
  if (!selected) return;
  const card = selected.closest('.painting-card');
  const img = card ? card.querySelector('img') : null;
  const els = getEditorElements();
  if (els.keyInput) els.keyInput.value = selected.value;
  if (els.deleteKeyInput) els.deleteKeyInput.value = selected.value;
  if (els.deleteForm) {
    els.deleteForm.style.display = selected.value.startsWith('local:') ? '' : 'none';
  }
  if (img && els.image) {
    els.image.src = img.src;
    els.image.onload = () => {
      applyEditorPreview();
    };
  }
  if (els.scale) els.scale.value = selected.dataset.scale || '1.0';
  if (els.offsetX) els.offsetX.value = String(Math.round((Number(selected.dataset.offsetX || '0')) * 100));
  if (els.offsetY) els.offsetY.value = String(Math.round((Number(selected.dataset.offsetY || '0')) * 100));
  applyEditorPreview();
  updateRangeOutputs(document);
}

document.addEventListener('input', updateRangeOutputs);
document.addEventListener('change', updateRangeOutputs);
document.addEventListener('DOMContentLoaded', () => {
  updateRangeOutputs();
  updateModeSections();
  updatePaintingSelection();
  loadEditorFromSelectedCard();

  document.querySelectorAll('details.card').forEach((details) => {
    details.open = false;
  });

  const modeField = document.querySelector('[name="content_mode"]');
  if (modeField) {
    modeField.addEventListener('change', updateModeSections);
  }

  document.querySelectorAll('.painting-card input[type="radio"]').forEach((input) => {
    input.addEventListener('change', () => {
      updatePaintingSelection();
      loadEditorFromSelectedCard();
    });
  });

  ['editor_scale', 'editor_offset_x', 'editor_offset_y'].forEach((name) => {
    const el = document.querySelector(`[name="${name}"]`);
    if (el) {
      el.addEventListener('input', () => {
        applyEditorPreview();
        updateRangeOutputs(document);
      });
      el.addEventListener('change', () => {
        applyEditorPreview();
        updateRangeOutputs(document);
      });
    }
  });

  window.addEventListener('resize', () => {
    applyEditorPreview();
  });

  document.querySelectorAll('[data-preset]').forEach((button) => {
    button.addEventListener('click', () => {
      const preset = button.dataset.preset;
      if (preset === 'birds') {
        setValue('content_mode', 'birds');
        setValue('layout_mode', 'framed');
        setValue('painting_cycle', false);
      } else
      if (preset === 'today-full') {
        setValue('window_mode', 'today');
        setValue('content_mode', 'birds');
        setValue('layout_mode', 'full');
      } else if (preset === 'today-framed') {
        setValue('window_mode', 'today');
        setValue('content_mode', 'birds');
        setValue('layout_mode', 'framed');
      } else if (preset === '24h-full') {
        setValue('window_mode', '24h');
        setValue('content_mode', 'birds');
        setValue('layout_mode', 'full');
      } else if (preset === '24h-framed') {
        setValue('window_mode', '24h');
        setValue('content_mode', 'birds');
        setValue('layout_mode', 'framed');
      } else if (preset === 'paintings-full') {
        setValue('content_mode', 'paintings');
        setValue('layout_mode', 'full');
        setValue('vangogh_painting', 'self_portrait_felt_hat');
      }
      updateModeSections();
      updatePaintingSelection();
      loadEditorFromSelectedCard();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });
});