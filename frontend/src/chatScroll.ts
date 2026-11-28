export function shouldFollowChatBottom(
  scrollHeight: number,
  scrollTop: number,
  clientHeight: number,
  threshold = 120,
): boolean {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}
