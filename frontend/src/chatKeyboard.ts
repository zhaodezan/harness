export type ChatKeyEvent = {
  key: string;
  shiftKey: boolean;
  preventDefault: () => void;
  nativeEvent: { isComposing?: boolean };
};

export function submitChatOnEnter(
  event: ChatKeyEvent,
  submit: () => void,
): void {
  if (
    event.key !== 'Enter' ||
    event.shiftKey ||
    event.nativeEvent.isComposing
  ) {
    return;
  }
  event.preventDefault();
  submit();
}
