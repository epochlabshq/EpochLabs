import { ImageResponse } from 'next/og';
import { GOFORGE_HERO } from '@/config/goforgeCopy';

export const alt = 'GoForge by Epoch Labs';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

// Static on purpose: nothing about a launch (name, ticker, CA) may appear in a card that is generated before
// or without its data, and no ticker is ever written with a "$".
export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%', height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
          background: '#2A1C0D', color: '#FEFAF0', padding: '72px 80px', fontFamily: 'sans-serif',
          border: '8px solid #E99F30',
        }}
      >
        <div style={{ display: 'flex', fontSize: 26, letterSpacing: 6, color: '#E99F30', textTransform: 'uppercase' }}>
          Epoch Labs · Launches
        </div>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', fontSize: 150, fontWeight: 700, lineHeight: 1 }}>{GOFORGE_HERO.title}</div>
          <div style={{ display: 'flex', fontSize: 48, fontStyle: 'italic', fontFamily: 'serif', color: '#E99F30', marginTop: 16 }}>
            {GOFORGE_HERO.subtitle}
          </div>
        </div>
        <div style={{ display: 'flex', fontSize: 26, color: '#B3A48C' }}>
          Every launch explained, win or lose. epochlabs.run/goforge
        </div>
      </div>
    ),
    size,
  );
}
