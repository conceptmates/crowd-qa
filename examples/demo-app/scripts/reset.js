const fs = require('fs');
const path = require('path');

fs.rmSync(path.join(__dirname, '..', 'data'), { recursive: true, force: true });
console.log('data/ removed. The next start creates a fresh database.');
