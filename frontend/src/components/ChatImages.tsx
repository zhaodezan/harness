import { useState } from 'react';
import './ChatImages.css';

export type ChatImage = { id: string; label: string; url: string };

const imageUrl = /^\/api\/chat-images\/(?:debug|messages\/[0-9a-f]{32}\/[0-9a-f]{32})\/([0-9a-f]{32})$/;

function Preview({ image }: { image: ChatImage }) {
  const [failed, setFailed] = useState(false);
  return <figure className="chat-image">
    {failed ? <p role="status">图片不可用</p> : <a href={image.url} target="_blank" rel="noopener noreferrer" aria-label={`查看大图：${image.label}`}>
      <img src={image.url} alt={image.label} loading="lazy" decoding="async" onError={() => setFailed(true)}/>
    </a>}
    <figcaption>{image.label}</figcaption>
  </figure>;
}

export function ChatImages({ images }: { images?: ChatImage[] }) {
  if (!Array.isArray(images) || !images.length) return null;
  const local = images.filter((item) => item && typeof item.label === 'string' && typeof item.url === 'string'
    && typeof item.id === 'string' && imageUrl.exec(item.url)?.[1] === item.id);
  if (!local.length) return null;
  return <div className="chat-images" aria-label="生成图片">
    {local.map((image) => <Preview key={image.url} image={image}/>)}
  </div>;
}
