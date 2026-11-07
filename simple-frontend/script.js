const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const addTextBtn = document.getElementById('addTextBtn');

// Set canvas dimensions
canvas.width = 800;
canvas.height = 600;

addTextBtn.addEventListener('click', () => {
  // Draw a simple text
  ctx.font = '24px Arial';
  ctx.fillStyle = 'black';
  ctx.fillText('Hello World', 100, 100);
});