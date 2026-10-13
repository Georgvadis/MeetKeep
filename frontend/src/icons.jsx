function Icon({ size = 16, children, fill = "none" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke={fill === "none" ? "currentColor" : "none"}
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  );
}

export const DownloadIcon = (props) => <Icon {...props}><path d="M12 4v11m-5-5 5 5 5-5M5 20h14" /></Icon>;
export const CheckIcon = (props) => <Icon {...props}><path d="M5 12l5 5 9-10" /></Icon>;
export const AlertIcon = (props) => <Icon {...props}><circle cx="12" cy="12" r="8" /><path d="M12 8v5M12 16h.01" /></Icon>;
export const LinkIcon = (props) => (
  <Icon {...props}>
    <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" />
    <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" />
  </Icon>
);
export const GearIcon = (props) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2-1.2L14.2 3h-4l-.4 2.6a7 7 0 0 0-2 1.2l-2.4-.9-2 3.4 2 1.5a7 7 0 0 0 0 2.4l-2 1.5 2 3.4 2.4-.9a7 7 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7 7 0 0 0 2-1.2l2.3.9 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z" />
  </Icon>
);
export const PlayIcon = (props) => <Icon fill="currentColor" {...props}><path d="M8 5v14l11-7z" /></Icon>;
export const StopIcon = (props) => <Icon fill="currentColor" size={12} {...props}><rect x="5" y="5" width="14" height="14" rx="2" /></Icon>;
export const CloseIcon = (props) => <Icon size={12} {...props}><path d="M6 6l12 12M18 6L6 18" /></Icon>;
export const PuzzleIcon = (props) => (
  <Icon {...props}>
    <path d="M9 4h4v2a2 2 0 1 0 4 0V4h3v6h-2a2 2 0 1 0 0 4h2v6h-6v-2a2 2 0 1 0-4 0v2H4v-6h2a2 2 0 1 0 0-4H4V4h5z" />
  </Icon>
);
export const KeyIcon = (props) => <Icon {...props}><circle cx="8" cy="15" r="4" /><path d="M11 12l9-9M16 7l3 3" /></Icon>;
