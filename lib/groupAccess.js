const fs = require('fs');
const path = require('path');
const FILE = path.join(__dirname, '../data/groupAccess.json');

function read() {
  try { return JSON.parse(fs.readFileSync(FILE, 'utf8')); }
  catch (_) { return { active: [] }; }
}
function write(data) {
  fs.mkdirSync(path.dirname(FILE), { recursive: true });
  fs.writeFileSync(FILE, JSON.stringify(data, null, 2));
}
module.exports = {
  isActive(id) { return read().active.includes(id); },
  setActive(id, active) {
    const data = read();
    data.active = [...new Set(data.active || [])];
    data.active = active ? [...data.active.filter(x => x !== id), id] : data.active.filter(x => x !== id);
    write(data);
  }
};
