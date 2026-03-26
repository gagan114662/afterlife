// Mock baileys (ESM module) before any imports
jest.mock('@whiskeysockets/baileys', () => ({}));

import { classifyMessage } from '../personal';
import type { WAMessage } from '@whiskeysockets/baileys';

function makeMsg(overrides: Partial<WAMessage> = {}): WAMessage {
  return {
    key: { remoteJid: '15550001234@s.whatsapp.net', fromMe: false, id: 'test-id' },
    messageTimestamp: 1700000000,
    message: null,
    ...overrides,
  } as unknown as WAMessage;
}

describe('classifyMessage', () => {
  it('returns null for messages without message field', () => {
    const msg = makeMsg({ message: undefined });
    expect(classifyMessage(msg)).toBeNull();
  });

  it('classifies plain text conversation', () => {
    const msg = makeMsg({ message: { conversation: 'Hello!' } });
    expect(classifyMessage(msg)).toEqual({ type: 'text', content: 'Hello!' });
  });

  it('classifies extended text message', () => {
    const msg = makeMsg({
      message: { extendedTextMessage: { text: 'Extended hello' } },
    });
    expect(classifyMessage(msg)).toEqual({ type: 'text', content: 'Extended hello' });
  });

  it('classifies audio message as voice_note', () => {
    const msg = makeMsg({ message: { audioMessage: {} } });
    expect(classifyMessage(msg)).toEqual({ type: 'voice_note', content: '[voice note]' });
  });

  it('classifies image message as photo', () => {
    const msg = makeMsg({ message: { imageMessage: { caption: 'Nice pic' } } });
    expect(classifyMessage(msg)).toEqual({ type: 'photo', content: 'Nice pic' });
  });

  it('uses default caption for image without caption', () => {
    const msg = makeMsg({ message: { imageMessage: {} } });
    expect(classifyMessage(msg)).toEqual({ type: 'photo', content: '[photo]' });
  });

  it('classifies video message as video', () => {
    const msg = makeMsg({ message: { videoMessage: { caption: 'Watch this' } } });
    expect(classifyMessage(msg)).toEqual({ type: 'video', content: 'Watch this' });
  });

  it('uses default caption for video without caption', () => {
    const msg = makeMsg({ message: { videoMessage: {} } });
    expect(classifyMessage(msg)).toEqual({ type: 'video', content: '[video]' });
  });

  it('returns null for unrecognized message types', () => {
    const msg = makeMsg({ message: { stickerMessage: {} } });
    expect(classifyMessage(msg)).toBeNull();
  });
});
