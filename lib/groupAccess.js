
const fs = require('fs');
const path = require('path');

const DIR = path.join(__dirname, '../data');
const FILE = path.join(DIR, 'groupAccess.json');

function read() {
    try {
        if (!fs.existsSync(FILE)) {
            fs.mkdirSync(DIR, { recursive: true });
            fs.writeFileSync(FILE, JSON.stringify({ active: [] }, null, 2));
            return { active: [] };
        }

        const data = JSON.parse(fs.readFileSync(FILE, 'utf8'));

        if (!data || !Array.isArray(data.active)) {
            return { active: [] };
        }

        return data;
    } catch (error) {
        console.error('[GroupAccess] Read error:', error);
        return { active: [] };
    }
}

function write(data) {
    fs.mkdirSync(DIR, { recursive: true });

    const tempFile = FILE + '.tmp';
    fs.writeFileSync(tempFile, JSON.stringify(data, null, 2));
    fs.renameSync(tempFile, FILE);
}

module.exports = {
    isActive(id) {
        return read().active.includes(id);
    },

    setActive(id, active) {
        const data = read();
        const groups = new Set(data.active);

        if (active) {
            groups.add(id);
        } else {
            groups.delete(id);
        }

        write({ active: [...groups] });

        console.log(
            `[GroupAccess] ${id}: ${active ? 'ACTIVE' : 'INACTIVE'}`
        );

        return read().active.includes(id) === active;
    }
};
