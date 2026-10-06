import type { Metadata } from 'next';
import { RADAR_HERO } from '@/config/radarCopy';

export const metadata: Metadata = {
  title: 'Meta Radar · Epoch Labs',
  description: `${RADAR_HERO.subtitle} ${RADAR_HERO.intro}`,
  alternates: { canonical: '/radar' },
  openGraph: {
    title: 'Meta Radar · Epoch Labs',
    description: RADAR_HERO.intro,
    url: '/radar',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Meta Radar · Epoch Labs',
    description: RADAR_HERO.intro,
  },
};

export default function RadarLayout({ children }: LayoutProps<'/radar'>) {
  return children;
}
