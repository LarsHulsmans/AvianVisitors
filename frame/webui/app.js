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

document.addEventListener('input', updateRangeOutputs);
document.addEventListener('change', updateRangeOutputs);
document.addEventListener('DOMContentLoaded', () => {
  updateRangeOutputs();

  document.querySelectorAll('[data-preset]').forEach((button) => {
    button.addEventListener('click', () => {
      const preset = button.dataset.preset;
      if (preset === 'today-full') {
        setValue('window_mode', 'today');
        setValue('layout_mode', 'full');
      } else if (preset === 'today-framed') {
        setValue('window_mode', 'today');
        setValue('layout_mode', 'framed');
      } else if (preset === '24h-full') {
        setValue('window_mode', '24h');
        setValue('layout_mode', 'full');
      } else if (preset === '24h-framed') {
        setValue('window_mode', '24h');
        setValue('layout_mode', 'framed');
      }
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });
});