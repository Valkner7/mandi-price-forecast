export default function StatusBanner({ message, isError, isWarn }) {
  if (!message) return null;
  return (
    <div className={'status-banner' + (isError ? ' error' : isWarn ? ' warn' : '')}>
      {message}
    </div>
  );
}
