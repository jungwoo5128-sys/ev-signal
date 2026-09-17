import type { Metadata } from "next";
import { Black_Han_Sans, Fragment_Mono } from "next/font/google";
import "./globals.css";

// 라벨·버튼·숫자용 고정폭 폰트 (한글은 Pretendard로 대체)
const fragmentMono = Fragment_Mono({
  variable: "--font-fragment-mono",
  weight: "400",
  subsets: ["latin"],
});

// 로고·형광펜 강조 단어용 굵은 한글 디스플레이 폰트
const blackHanSans = Black_Han_Sans({
  variable: "--font-black-han-sans",
  weight: "400",
  preload: false,
});

export const metadata: Metadata = {
  title: "전기차 신호등",
  description: "내 조건으로 전기차 전환 여부를 초록·노랑·빨강 신호로 판정합니다",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className={`${fragmentMono.variable} ${blackHanSans.variable}`}>
      <head>
        {/* 한글 본문 폰트. 필요한 글자만 받는 dynamic subset */}
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
