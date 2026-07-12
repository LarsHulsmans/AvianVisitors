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
  // Limit translation to the extra area introduced by zoom.
  const maxShift = Math.max(0, (scale - 1) * 50);
  const shiftX = (Math.max(-100, Math.min(100, offsetX)) / 100) * maxShift;
  const shiftY = (Math.max(-100, Math.min(100, offsetY)) / 100) * maxShift;
  els.image.style.transform = `translate(${shiftX}%, ${shiftY}%) scale(${scale})`;
  els.image.style.transformOrigin = 'center center';
  if (els.saveScale) els.saveScale.value = String(scale);
  if (els.saveX) els.saveX.value = String(offsetX);
  if (els.saveY) els.saveY.value = String(offsetY);
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

  document.querySelectorAll('[data-preset]').forEach((button) => {
    button.addEventListener('click', () => {
      const preset = button.dataset.preset;
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