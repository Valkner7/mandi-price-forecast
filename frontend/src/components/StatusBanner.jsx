export default function StatusBanner({ message, isError }) {
  if (!message) return null;
  return (
    <div className={'status-banner' + (isError ? ' error' : '')}>
      {message}
    </div>
  );
}
