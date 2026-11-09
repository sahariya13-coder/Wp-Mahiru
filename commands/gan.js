const axios = require('axios');
const yts = require('yt-search');
const settings = require('../settings');
const { toAudio } = require('../lib/converter');

const WORKER_URL = String(settings.youtubeWorkerUrl || '').replace(/\/$/, '');

function extractYouTubeUrl(text) {
  const match = String(text || '').match(/https?:\/\/[^\s]+/i);
  if (match && /youtube\.com|youtu\.be/i.test(match[0])) return match[0].replace(/[)>\]}.,]+$/, '');
  return null;
}

function detectAudioFormat(buffer) {
  if (!buffer || buffer.length < 12) return null;
  if (buffer.toString('ascii', 4, 8) === 'ftyp') return { ext: 'm4a', mime: 'audio/mp4' };
  if (buffer.toString('ascii', 0, 3) === 'ID3' || (buffer[0] === 0xff && (buffer[1] & 0xe0) === 0xe0)) {
    return { ext: 'mp3', mime: 'audio/mpeg' };
  }
  if (buffer.toString('ascii', 0, 4) === 'OggS') return { ext: 'ogg', mime: 'audio/ogg' };
  if (buffer.toString('ascii', 0, 4) === 'RIFF') return { ext: 'wav', mime: 'audio/wav' };
  if (buffer.readUInt32BE(0) === 0x1a45dfa3) return { ext: 'webm', mime: 'audio/webm' };
  return null;
}

async function ganCommand(sock, chatId, message) {
  try {
    const raw = message.message?.conversation ||
      message.message?.extendedTextMessage?.text ||
      message.message?.imageMessage?.caption || '';
    const query = raw.replace(/^\.gan\s*/i, '').trim();

    if (!query) {
      await sock.sendMessage(chatId, {
        text: 'ব্যবহার: .gan <গানের নাম অথবা YouTube link>'
      }, { quoted: message });
      return;
    }

    if (!WORKER_URL) throw new Error('YouTube Worker URL settings.js-এ সেট করা নেই।');

    let video;
    const directUrl = extractYouTubeUrl(query);

    if (directUrl) {
      video = { url: directUrl, title: 'YouTube Audio', timestamp: '—' };
      try {
        const id = (directUrl.match(/(?:youtu\.be\/|[?&]v=|\/shorts\/)([\w-]{11})/) || [])[1];
        if (id) {
          const info = await yts({ videoId: id });
          if (info?.title) video = { ...video, title: info.title, timestamp: info.timestamp || '—' };
        }
      } catch (_) {}
    } else {
      const search = await yts(query);
      video = search?.videos?.[0];
      if (!video?.url) {
        await sock.sendMessage(chatId, { text: '❌ YouTube-এ কোনো গান পাওয়া যায়নি।' }, { quoted: message });
        return;
      }
    }

    await sock.sendMessage(chatId, {
      text: `🎵 *গান:* ${video.title || 'YouTube Audio'}\n⏳ Audio খোঁজা হচ্ছে...`
    }, { quoted: message });

    const apiUrl = `${WORKER_URL}/api/formats?url=${encodeURIComponent(video.url)}`;
    const apiResponse = await axios.get(apiUrl, {
      timeout: 90000,
      headers: { Accept: 'application/json' }
    });
    const payload = apiResponse.data;

    if (!payload?.success || !Array.isArray(payload.formats)) {
      throw new Error(payload?.error || 'Worker থেকে audio format পাওয়া যায়নি।');
    }

    const audioFormats = payload.formats.filter((format) => {
      const kind = String(format.kind || '').toLowerCase();
      const label = String(format.label || '').toLowerCase();
      const audioOnly = kind === 'audio' || kind.includes('audio-only') ||
        kind.includes('audio_only') || (!kind.includes('video') && /mp3|m4a|opus|ogg|audio/.test(label));
      return audioOnly && /^https?:\/\//i.test(String(format.download || ''));
    });

    if (!audioFormats.length) {
      throw new Error('Worker কোনো direct audio-only download link দেয়নি।');
    }

    const selected = audioFormats.find((format) => /mp3/i.test(String(format.label || ''))) ||
      audioFormats.find((format) => /m4a|mp4/i.test(String(format.label || ''))) ||
      audioFormats[0];

    const downloadResponse = await axios.get(selected.download, {
      responseType: 'arraybuffer',
      timeout: 120000,
      maxRedirects: 5,
      maxContentLength: 50 * 1024 * 1024,
      maxBodyLength: 50 * 1024 * 1024,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
        Accept: 'audio/*,application/octet-stream,*/*'
      }
    });

    let audioBuffer = Buffer.from(downloadResponse.data);
    const detected = detectAudioFormat(audioBuffer);
    if (!detected) {
      throw new Error('Download করা ফাইলটি valid audio নয়। Worker-এর download link পরীক্ষা করুন।');
    }

    if (detected.ext !== 'mp3') {
      audioBuffer = await toAudio(audioBuffer, detected.ext);
      if (!audioBuffer?.length) throw new Error('Audio MP3-তে convert করা যায়নি।');
    }

    const safeTitle = String(video.title || 'Mahiru Song')
      .replace(/[^\w\s-]/g, '')
      .trim()
      .slice(0, 80) || 'Mahiru Song';

    await sock.sendMessage(chatId, {
      audio: audioBuffer,
      mimetype: 'audio/mpeg',
      fileName: `${safeTitle}.mp3`,
      ptt: false
    }, { quoted: message });

  } catch (error) {
    console.error('[gan] Download error:', error?.response?.status || error?.message || error);
    const detail = error?.response?.data?.error || error?.message || 'Unknown error';
    await sock.sendMessage(chatId, {
      text: `❌ গান Download করা যায়নি।\nকারণ: ${String(detail).slice(0, 250)}`
    }, { quoted: message });
  }
}

module.exports = ganCommand;
