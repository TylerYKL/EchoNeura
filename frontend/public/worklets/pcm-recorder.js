/**
 * AudioWorklet processor: Float32 mic frames -> Int16 PCM chunks.
 *
 * Runs on the audio thread; posts transferable buffers to the main thread,
 * which forwards them straight to the WebSocket. The server contract is mono
 * signed-16-bit little-endian at the AudioContext's sample rate (Int16Array is
 * little-endian on every platform browsers run on).
 */
class PCMRecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) {
      return true;
    }
    const channel = input[0];
    const pcm = new Int16Array(channel.length);
    for (let i = 0; i < channel.length; i += 1) {
      const s = Math.max(-1, Math.min(1, channel[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    return true;
  }
}

registerProcessor("pcm-recorder", PCMRecorderProcessor);
