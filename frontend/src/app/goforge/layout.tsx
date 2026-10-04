import type { Metadata } from 'next';
import { GOFORGE_HERO } from '@/config/goforgeCopy';

export const metadata: Metadata = {
  title: 'GoForge · Epoch Labs',
  description: `${GOFORGE_HERO.subtitle} ${GOFORGE_HERO.intro}`,
  alternates: { canonical: '/goforge' },
  openGraph: {
    title: 'GoForge · Epoch Labs',
    description: GOFORGE_HERO.intro,
    url: '/goforge',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'GoForge · Epoch Labs',
    description: GOFORGE_HERO.intro,
  },
};

export default function GoForgeLayout({ children }: LayoutProps<'/goforge'>) {
  return children;
}
