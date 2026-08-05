import React from 'react';
import {
  AbsoluteFill,
  Easing,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

const colors = {
  ink: '#172b2d',
  deep: '#10251f',
  mint: '#dceee8',
  mintLight: '#eff8f5',
  green: '#176b5b',
  bright: '#72cfae',
  gold: '#f5bb54',
  warm: '#efe9df',
  muted: '#78908c',
  white: '#ffffff',
};

const sceneStarts = [0, 240, 540, 900, 1200, 1530, 1830, 2040, 2280];

const clamp = (value: number) => Math.max(0, Math.min(1, value));

const progress = (frame: number, start: number, duration: number) =>
  clamp((frame - start) / duration);

const enter = (frame: number, start: number, duration = 22) => {
  const {fps} = useVideoConfig();
  return spring({frame: Math.max(0, frame - start), fps, config: {damping: 200, mass: 0.8}});
};

const fade = (frame: number, start: number, end: number) =>
  interpolate(frame, [start, start + 16, end, end + 16], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.ease),
  });

const Wordmark: React.FC<{light?: boolean}> = ({light = false}) => (
  <Img src={staticFile('vitamine-logo.png')} style={{width: 164, height: 92, objectFit: 'contain', objectPosition: 'left center', filter: light ? 'brightness(0) invert(1)' : undefined}} />
);

const Headline: React.FC<{eyebrow?: string; title: React.ReactNode; body?: string; light?: boolean; align?: 'left' | 'center'}> = ({eyebrow, title, body, light = false, align = 'left'}) => (
  <div style={{maxWidth: 840, textAlign: align, color: light ? colors.white : colors.ink}}>
    {eyebrow ? <div style={{marginBottom: 20, color: light ? colors.bright : colors.green, fontSize: 20, fontWeight: 800, letterSpacing: '0.14em', textTransform: 'uppercase'}}>{eyebrow}</div> : null}
    <div style={{fontSize: 78, fontWeight: 760, letterSpacing: '-0.065em', lineHeight: 0.95}}>{title}</div>
    {body ? <div style={{marginTop: 28, color: light ? '#b9d1c8' : '#5d726f', fontSize: 29, lineHeight: 1.35}}>{body}</div> : null}
  </div>
);

const Frame: React.FC<{children: React.ReactNode; background: string; opacity?: number}> = ({children, background, opacity = 1}) => (
  <AbsoluteFill style={{background, opacity, overflow: 'hidden', fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'}}>{children}</AbsoluteFill>
);

const Topline: React.FC<{light?: boolean; label: string}> = ({light, label}) => (
  <div style={{position: 'absolute', top: 58, left: 80, right: 80, display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
    <Wordmark light={light} />
    <div style={{color: light ? '#b7d5cb' : colors.muted, fontSize: 18, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase'}}>{label}</div>
  </div>
);

const LandingArrowhead: React.FC<{x: number; y: number; angle: number; arrival: number; color: string; length: number; width: number}> = ({x, y, angle, arrival, color, length, width}) => {
  if (arrival <= 0) return null;
  const scale = interpolate(arrival, [0, .42, .76, 1], [.25, 1.24, .92, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <path d={`M0 0 L-${length} -${width} L-${length} ${width} Z`} fill={color} opacity={Math.min(1, arrival * 4)} transform={`translate(${x} ${y}) rotate(${angle}) scale(${scale})`} />;
};

const RequestCard: React.FC<{x: number; y: number; label: string; text: string; visible: number}> = ({x, y, label, text, visible}) => (
  <div style={{position: 'absolute', left: `${x}%`, top: `${y}%`, width: 278, padding: '18px 20px', borderRadius: 16, background: 'rgba(255,255,255,.94)', boxShadow: '0 18px 45px rgba(23,45,41,.15)', opacity: visible, transform: `translateY(${(1 - visible) * -40}px) rotate(${(x - 50) / 18}deg)`}}>
    <div style={{color: colors.green, fontSize: 14, fontWeight: 800, letterSpacing: '.1em', textTransform: 'uppercase'}}>{label}</div>
    <div style={{marginTop: 8, color: colors.ink, fontSize: 17, fontWeight: 700, lineHeight: 1.25}}>{text}</div>
  </div>
);

const ProblemScene: React.FC<{frame: number}> = ({frame}) => {
  const local = frame - sceneStarts[1];
  const opacity = fade(frame, sceneStarts[1], sceneStarts[2]);
  const requests = [
    ['NIH', 'A biosketch in the updated format.'],
    ['Prize committee', 'A CV in French, two pages maximum.'],
    ['Faculty affairs', 'A tabulated CV of one page maximum.'],
    ['Promotion', 'Include mentoring successes.'],
    ['Conference', 'A 150-word speaker biography by tomorrow.'],
    ['Foundation', 'Ten selected papers and your contribution.'],
    ['University website', 'A short bio, metrics and recent papers.'],
    ['Review committee', 'Separate talks, keynotes and presentations.'],
    ['Collaborator', 'The long version with DOI links in Word.'],
    ['Deadline reminder', 'Upload a signed and dated CV today.'],
  ];
  const bottle = enter(local, 150);
  const clear = progress(local, 193, 38);
  return <Frame background={colors.mint} opacity={opacity}>
    <Topline label="One source of truth" />
    <div style={{position: 'absolute', left: 100, top: 210, width: 810}}><Headline title={<>Stop recounting your career<br />over and over again.</>} body="Every opportunity asks for a different version of the same academic record." /></div>
    <div style={{position: 'absolute', left: 1030, top: 190, width: 770, height: 720, borderRadius: 48, background: 'linear-gradient(145deg,#f5fbf8,#d0e6df)', boxShadow: 'inset 0 0 100px rgba(57,124,106,.12)'}}>
      <Img src={staticFile('researcher-at-desk.svg')} style={{position: 'absolute', left: 110, bottom: 28, width: 430, height: 520, objectFit: 'contain', filter: `drop-shadow(0 0 ${clear * 30}px rgba(43,184,139,.7))`}} />
      {requests.map(([label, text], index) => <RequestCard key={label} x={[2, 57, 7, 59, 1, 56, 16, 62, 4, 53][index]} y={[2, 8, 22, 28, 43, 48, 62, 66, 79, 83][index]} label={label} text={text} visible={enter(local, index * 12 + 5) * (1 - clear)} />)}
      <div style={{position: 'absolute', left: 338, top: 210, width: 130, height: 196, border: `5px solid ${colors.ink}`, borderRadius: '25px 25px 38px 38px', background: `linear-gradient(140deg,${colors.gold},#e2931c)`, transform: `translate(${(1 - bottle) * 300}px,${(1 - bottle) * -170}px) rotate(${-42 * bottle}deg) scale(${1 - clear * .3})`, opacity: bottle * (1 - clear), boxShadow: '0 22px 45px rgba(36,71,61,.22)'}}>
        <div style={{position: 'absolute', top: -30, left: 42, width: 76, height: 30, borderRadius: '8px 8px 2px 2px', background: colors.ink}} />
        <div style={{position: 'absolute', inset: '47px 12px 32px', display: 'grid', placeItems: 'center', borderRadius: 13, background: colors.white, color: colors.ink, fontSize: 19, fontWeight: 850, letterSpacing: '-.08em'}}>VitaMine</div>
      </div>
      {clear > 0 ? <div style={{position: 'absolute', inset: 0, borderRadius: 48, background: `radial-gradient(circle at 48% 54%,rgba(114,207,174,${clear * .35}),transparent ${25 + clear * 24}%)`, pointerEvents: 'none'}} /> : null}
    </div>
  </Frame>;
};

const ImportScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[2];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[3]);
  const chips = ['47 publications', '12 positions', '18 grants', '26 invited talks', '9 mentees', '7 awards', '14 teaching activities', '32 collaborators', '11 committee roles', 'Skills and languages', 'Education and training', 'Professional memberships'];
  return <Frame background={colors.deep} opacity={opacity}>
    <Topline light label="Get started in seconds" />
    <div style={{position: 'absolute', left: 100, top: 250}}><Headline light eyebrow="Start with the CV you have" title={<>Keep exactly one record<br />of your CV. Ready to grow.</>} body="VitaMine extracts the details and organizes them. You can always download your data in a portable format." /></div>
    <div style={{position: 'absolute', left: 1060, top: 170, width: 700, height: 760}}>
      <div style={{position: 'absolute', left: 0, top: 155, width: 270, height: 350, display: 'grid', placeItems: 'center', border: '3px dashed rgba(130,212,184,.65)', borderRadius: 28, color: '#b9d9ce', fontSize: 21, fontWeight: 700, opacity: enter(local, 8)}}>Drop your CV here</div>
      <div style={{position: 'absolute', left: 70, top: 35, width: 235, height: 300, borderRadius: 10, padding: 32, background: colors.white, color: colors.ink, transform: `translate(${interpolate(progress(local, 4, 105), [0, 1], [-30, 18])}px,${interpolate(progress(local, 4, 105), [0, 1], [-120, 160])}px) scale(${interpolate(progress(local, 105, 55), [0, 1], [1, .7])})`, opacity: 1 - progress(local, 122, 55), boxShadow: '0 28px 70px rgba(0,0,0,.32)'}}>
        <div style={{display: 'grid', placeItems: 'center', width: 48, height: 48, borderRadius: 7, background: '#2b579a', color: colors.white, fontWeight: 900}}>W</div><div style={{marginTop: 24, fontWeight: 800}}>academic-cv.docx</div><div style={{height: 7, marginTop: 25, borderRadius: 9, background: '#dbe4e1'}} /><div style={{height: 7, marginTop: 12, width: '74%', borderRadius: 9, background: '#dbe4e1'}} /><div style={{height: 7, marginTop: 12, borderRadius: 9, background: '#dbe4e1'}} />
      </div>
      <div style={{position: 'absolute', right: 55, top: 225, display: 'grid', placeItems: 'center', width: 235, height: 235, border: '2px solid rgba(130,212,184,.45)', borderRadius: '50%', background: 'rgba(130,212,184,.1)', boxShadow: '0 0 85px rgba(85,199,157,.24)', color: colors.white, transform: `scale(${interpolate(progress(local, 108, 70), [0, 1], [.75, 1])})`}}><div style={{textAlign: 'center'}}><div style={{fontSize: 52}}>◫</div><b style={{fontSize: 28}}>.vitamine</b><div style={{marginTop: 7, color: '#aecbc1', fontSize: 15}}>your portable database</div></div></div>
      {chips.map((chip, index) => <div key={chip} style={{position: 'absolute', left: [10, 340, 435, 15, 290, 430, 22, 285, 472, 40, 265, 455][index], top: [585, 70, 530, 665, 625, 680, 120, 185, 478, 735, 744, 605][index], border: '1px solid rgba(255,255,255,.18)', borderRadius: 999, padding: '11px 16px', background: 'rgba(255,255,255,.09)', color: colors.white, fontSize: 16, fontWeight: 700, opacity: enter(local, 145 + index * 8), transform: `translateY(${(1 - enter(local, 145 + index * 8)) * 28}px)`}}>{chip}</div>)}
    </div>
  </Frame>;
};

const SyncScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[3];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[4]);
  const nodes = [{x: 135, y: 450, text: 'iD', color: '#6aaf38'}, {x: 450, y: 105, text: 'Scholar', color: '#4774b8'}, {x: 765, y: 450, text: 'Z', color: '#aa3330'}, {x: 450, y: 790, text: 'OpenAlex', color: '#7765a7'}];
  const links = [
    {d: 'M193 450 C245 410 278 422 310 450', x: 310, y: 450, angle: 0},
    {d: 'M450 163 C430 236 438 278 450 310', x: 450, y: 310, angle: 90},
    {d: 'M707 450 C640 420 612 425 590 450', x: 590, y: 450, angle: 180},
    {d: 'M450 719 C470 650 462 620 450 590', x: 450, y: 590, angle: -90},
  ];
  return <Frame background={colors.mint} opacity={opacity}>
    <Topline label="Always up-to-date" />
    <div style={{position: 'absolute', left: 100, top: 300}}><Headline eyebrow="Your academic ecosystem" title={<>Keep your record up-to-date.<br />Effortlessly.</>} body="Connect VitaMine with the scholarly services you already trust." /></div>
    <div style={{position: 'absolute', left: 955, top: 80, width: 900, height: 900}}>
      <svg viewBox="0 0 900 900" width="100%" height="100%" style={{overflow: 'visible'}}>
        <circle cx="450" cy="450" r="180" fill="none" stroke="rgba(23,107,91,.18)" strokeWidth="2" /><circle cx="450" cy="450" r="345" fill="none" stroke="rgba(23,107,91,.18)" strokeWidth="2" />
        {links.map((link, index) => {
          const routeStart = 20 + index * 28;
          const visible = progress(local, routeStart, 80);
          const arrowArrival = progress(local, routeStart + 80, 16);
          return <React.Fragment key={link.d}>
            <path d={link.d} fill="none" stroke={colors.green} strokeWidth="9" strokeLinecap="round" strokeDasharray="1" pathLength="1" strokeDashoffset={1 - visible} />
            <LandingArrowhead x={link.x} y={link.y} angle={link.angle} arrival={arrowArrival} color={colors.green} length={29} width={16} />
          </React.Fragment>;
        })}
        <circle cx="450" cy="450" r="126" fill={colors.white} style={{filter: 'drop-shadow(0 24px 35px rgba(23,67,58,.16))'}} />
        <text x="450" y="440" textAnchor="middle" fill={colors.green} fontSize="52">▤</text><text x="450" y="485" textAnchor="middle" fill={colors.ink} fontSize="22" fontWeight="800">Your living CV</text>
        {nodes.map((node, index) => <g key={node.text} opacity={enter(local, 13 + index * 20)} transform={`translate(${node.x} ${node.y}) scale(${enter(local, 13 + index * 20)})`}><circle r={node.text === 'OpenAlex' ? 67 : 54} fill={colors.white} style={{filter: 'drop-shadow(0 16px 24px rgba(23,67,58,.14))'}} /><text y="7" textAnchor="middle" fill={node.color} fontSize={node.text.length > 3 ? 17 : 30} fontWeight="800">{node.text}</text></g>)}
      </svg>
    </div>
  </Frame>;
};

const PromptScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[4];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[5]);
  const text = 'Keep the CV to a maximum of two pages. Include no more than 10 publications. Prefer recent first- or last-authorships with high impact factors. Definitely include the paper first-authored by Marie Curie. Use APA 7th edition and add DOI links.';
  const typed = text.slice(0, Math.floor(text.length * progress(local, 28, 205)));
  return <Frame background={colors.deep} opacity={opacity}>
    <Topline light label="Prompt-guided export" />
    <div style={{position: 'absolute', left: 100, top: 285}}><Headline light eyebrow="Simply describe what you need" title={<>Export your CV in<br />a custom format.</>} body="Format, focus, language, citation style—put it in plain words." /></div>
    <div style={{position: 'absolute', left: 970, top: 235, width: 790, borderRadius: 25, overflow: 'hidden', background: '#fcfdfd', color: colors.ink, boxShadow: '0 38px 100px rgba(0,0,0,.35)', transform: `translateY(${(1 - enter(local, 0)) * 70}px) rotate(-2deg)`, opacity: enter(local, 0)}}>
      <div style={{height: 66, display: 'flex', alignItems: 'center', gap: 10, padding: '0 22px', borderBottom: '1px solid #dee8e6', background: '#edf2f1'}}><i style={{width: 12, height: 12, borderRadius: '50%', background: '#e88976'}} /><i style={{width: 12, height: 12, borderRadius: '50%', background: '#e9c46b'}} /><i style={{width: 12, height: 12, borderRadius: '50%', background: '#78bd99'}} /><span style={{marginLeft: 12, color: '#657b77', fontWeight: 700}}>Describe your export</span></div>
      <div style={{minHeight: 355, padding: 42, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 20, lineHeight: 1.5}}>{typed}<span style={{display: 'inline-block', width: 3, height: 23, marginLeft: 5, background: colors.green, verticalAlign: -4}} /></div>
      <div style={{display: 'flex', justifyContent: 'flex-end', padding: '20px 27px', borderTop: '1px solid #e1ebea', color: '#657b77', fontSize: 17}}><b style={{borderRadius: 8, padding: '11px 16px', background: colors.green, color: colors.white}}>Generate CV ↗</b></div>
    </div>
  </Frame>;
};

const ProfileScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[5];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[6]);
  return <Frame background={colors.warm} opacity={opacity}>
    <Topline label="A living academic profile" />
    <div style={{position: 'absolute', left: 100, top: 250}}><Headline eyebrow="Share the work behind the CV" title={<>An academic website<br />that you will never have<br />to maintain anymore.</>} body="Host it with VitaMine, or embed selected content wherever your work lives." /></div>
    <div style={{position: 'absolute', left: 960, top: 185, width: 805, height: 675, borderRadius: 24, overflow: 'hidden', background: colors.white, boxShadow: '0 35px 90px rgba(86,64,40,.2)', transform: `translateY(${(1 - enter(local, 0)) * 70}px)`, opacity: enter(local, 0)}}>
      <div style={{height: 57, display: 'flex', alignItems: 'center', gap: 12, padding: '0 20px', borderBottom: '1px solid #ece9e3', background: '#f7f5f1'}}><i style={{width: 11, height: 11, borderRadius: '50%', background: '#d4c9ba'}} /><div style={{flex: 1, borderRadius: 6, padding: '7px', background: colors.white, color: '#8b847a', fontSize: 14, textAlign: 'center'}}>vitamine.cloud/a-researcher</div></div>
      <div style={{padding: '30px 38px'}}>
        <div style={{display: 'flex', borderBottom: '1px solid #edf0ee', paddingBottom: 20, color: '#71817d', fontSize: 15, fontWeight: 700}}><b style={{marginRight: 'auto', color: colors.ink, fontSize: 23}}>AR<span style={{color: colors.green}}>.</span></b><span>About</span><span style={{marginLeft: 27}}>Research</span><span style={{marginLeft: 27}}>Publications</span></div>
        <div style={{display: 'flex', gap: 28, padding: '30px 0 24px', opacity: enter(local, 24), transform: `translateY(${(1 - enter(local, 24)) * 18}px)`}}><div style={{width: 116, height: 130, overflow: 'hidden', borderRadius: '58px 58px 16px 16px', background: '#cfe6de'}}><svg viewBox="0 0 120 140" width="100%" height="100%"><path d="M7 66C7 24 30 5 62 5c34 0 52 22 52 61v68H7Z" fill="#cee3dc"/><path d="M19 137c3-29 17-42 41-42s39 13 43 42Z" fill="#214f46"/><path d="m47 96 13 22 14-22Z" fill="#f3b95f"/><path d="M49 82h23v24H49Z" fill="#b96f52"/><path d="M35 49c0-25 12-36 27-36 20 0 29 15 27 39-2 27-13 43-28 43S35 76 35 49Z" fill="#b96f52"/><path d="M32 56c-5-27 5-48 31-48 24 0 33 19 28 43-8-5-12-15-15-24-10 12-25 19-44 20Z" fill="#26363a"/><path d="M39 53h18v12H39Zm26 0h18v12H65Zm-8 5h8" fill="none" stroke="#26363a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/><path d="M53 77q8 6 16 0" fill="none" stroke="#26363a" strokeWidth="2.5" strokeLinecap="round"/></svg></div><div><div style={{color: colors.green, fontSize: 14, fontWeight: 800, letterSpacing: '.08em'}}>PROFESSOR OF SYSTEMS NEUROSCIENCE</div><div style={{marginTop: 8, fontSize: 34, fontWeight: 800, letterSpacing: '-.05em'}}>Alex Researcher</div><div style={{marginTop: 9, color: '#6f7b78', fontSize: 17}}>Northbridge University · Center for Neural Dynamics</div><div style={{marginTop: 13, color: '#64726f', fontSize: 15, lineHeight: 1.35}}>Alex studies how distributed brain networks shape movement, learning and recovery.</div><div style={{marginTop: 14, display: 'flex', gap: 8}}>{['Neural networks', 'Movement', 'Brain stimulation'].map((tag) => <span key={tag} style={{borderRadius: 999, padding: '7px 10px', background: '#e8f1ee', color: colors.green, fontSize: 13, fontWeight: 700}}>{tag}</span>)}</div></div></div>
        <div style={{display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', borderTop: '1px solid #e5e8e7', borderBottom: '1px solid #e5e8e7', padding: '18px 0', opacity: enter(local, 50)}}>{[['82', 'Publications'], ['4,218', 'Citations'], ['32', 'Collaborators'], ['11', 'Mentees']].map(([number, label]) => <div key={label}><b style={{fontSize: 27}}>{number}</b><span style={{display: 'block', marginTop: 4, color: '#6f7b78', fontSize: 13}}>{label}</span></div>)}</div>
      </div>
    </div>
  </Frame>;
};

const InsightScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[6];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[7]);
  const graph = 'M90 410 C210 390 270 340 365 350 S520 310 620 250 S790 280 900 120';
  const impact = progress(local, 30, 135);
  const citations = Math.round(4218 * impact).toLocaleString();
  const publications = String(Math.round(82 * impact));
  return <Frame background="#e2e8f0" opacity={opacity}>
    <Topline label="See the whole picture" />
    <div style={{position: 'absolute', left: 100, top: 315}}><Headline eyebrow="A record you can understand" title={<>Track your impact.</>} body="See your publications, citations and career trajectory in context." /></div>
    <div style={{position: 'absolute', left: 1010, top: 250, width: 730, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 22}}>
      {[[citations, 'Citations'], [publications, 'Publications']].map(([number, label], index) => <div key={label} style={{padding: 31, borderRadius: 22, background: 'rgba(255,255,255,.84)', boxShadow: '0 18px 48px rgba(48,64,78,.1)', opacity: enter(local, index * 10), transform: `translateY(${(1 - enter(local, index * 10)) * 24}px)`}}><span style={{color: '#6d7d85', fontWeight: 700}}>{label}</span><b style={{display: 'block', marginTop: 14, fontSize: 58, letterSpacing: '-.07em'}}>{number}</b><small style={{color: colors.green, fontWeight: 700}}>Growing with your work</small></div>)}
      <div style={{gridColumn: '1 / -1', padding: 32, borderRadius: 22, background: 'rgba(255,255,255,.84)', boxShadow: '0 18px 48px rgba(48,64,78,.1)', opacity: enter(local, 22)}}><span style={{color: '#6d7d85', fontWeight: 700}}>Citations over time</span><svg viewBox="0 0 1000 470" width="100%" height="290" style={{marginTop: 20, overflow: 'visible'}}><path d={`${graph} L900 450 L90 450 Z`} fill="rgba(114,207,174,.18)" /><path d={graph} fill="none" stroke={colors.green} strokeWidth="12" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - progress(local, 30, 135)} /></svg></div>
    </div>
  </Frame>;
};

const MapScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[7];
  const local = frame - start;
  const opacity = fade(frame, start, sceneStarts[8]);
  const routes = [
    {d: 'M519 109 Q405 35 305 127', x: 305, y: 127, angle: 137},
    {d: 'M519 109 Q702 45 875 144', x: 875, y: 144, angle: 30},
    {d: 'M519 109 Q712 232 906 335', x: 906, y: 335, angle: 28},
    {d: 'M519 109 Q500 235 545 330', x: 545, y: 330, angle: 65},
    {d: 'M294 137 Q202 230 294 331', x: 294, y: 331, angle: 46},
    {d: 'M294 137 Q222 104 177 135', x: 177, y: 135, angle: 146},
    {d: 'M888 151 Q805 238 722 272', x: 722, y: 272, angle: 158},
  ];
  const points = [[519, 109], [294, 137], [888, 151], [919, 342], [551, 344], [304, 342], [165, 143], [708, 278]];
  return <Frame background="#13242c" opacity={opacity}>
    <Topline light label="Your network, visualized" />
    <div style={{position: 'absolute', left: 100, top: 300}}><Headline light eyebrow="Collaboration without borders" title={<>See where your<br />research connects.</>} body="Explore the people and places behind your academic network." /></div>
    <div style={{position: 'absolute', right: 70, top: 210, width: 950, height: 590}}>
      <Img src={staticFile('world-map.svg')} style={{position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'contain'}} />
      <svg viewBox="0 0 1000 500" width="100%" height="100%" style={{position: 'absolute', inset: 0}}>{routes.map((route, index) => {
        const routeStart = 25 + Math.floor(index / 2) * 34;
        const visible = progress(local, routeStart, 55);
        const arrowArrival = progress(local, routeStart + 55, 14);
        return <React.Fragment key={route.d}>
          <path d={route.d} fill="none" stroke={colors.bright} strokeWidth="2.5" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - visible} />
          <LandingArrowhead x={route.x} y={route.y} angle={route.angle} arrival={arrowArrival} color={colors.bright} length={15} width={8} />
        </React.Fragment>;
      })}{points.map(([x, y], index) => <circle key={`${x}-${y}`} cx={x} cy={y} r={index === 0 ? 8 : 6} fill="#d7f3e9" stroke={colors.bright} strokeWidth="4" opacity={enter(local, index * 13)} />)}</svg>
      <div style={{position: 'absolute', right: 40, bottom: 22, borderRadius: 15, padding: '16px 20px', background: 'rgba(16,37,44,.86)', color: colors.white}}><b style={{fontSize: 24}}>18 collaborators</b><span style={{display: 'block', marginTop: 4, color: '#9bb1b7'}}>across the world</span></div>
    </div>
  </Frame>;
};

const ClosingScene: React.FC<{frame: number}> = ({frame}) => {
  const start = sceneStarts[8];
  const opacity = interpolate(frame, [start, start + 20], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <Frame background={colors.mint} opacity={opacity}>
    <div style={{position: 'absolute', inset: 0, background: 'radial-gradient(circle at 50% 44%,rgba(255,255,255,.9),transparent 32%)'}} />
    <div style={{position: 'absolute', top: 260, left: 0, right: 0, display: 'grid', placeItems: 'center', textAlign: 'center', transform: `translateY(${(1 - enter(frame, start + 8)) * 35}px)`, opacity: enter(frame, start + 8)}}><Wordmark /><div style={{marginTop: 45, color: colors.ink, fontSize: 84, fontWeight: 800, letterSpacing: '-.07em', lineHeight: .92}}>Your Research Career.<br />In Any Format.</div><div style={{marginTop: 28, color: '#58706b', fontSize: 29}}>Ready for what comes next.</div><div style={{marginTop: 50, borderRadius: 999, padding: '17px 28px', background: colors.green, color: colors.white, fontSize: 21, fontWeight: 800}}>vitamine.cloud</div></div>
  </Frame>;
};

export const PromoFilm: React.FC = () => {
  const frame = useCurrentFrame();
  return <AbsoluteFill>
    <Frame background={colors.mint} opacity={fade(frame, sceneStarts[0], sceneStarts[1])}>
      <Topline label="Academic CV manager" />
      <div style={{position: 'absolute', top: 290, left: 100, zIndex: 2, transform: `translateY(${(1 - enter(frame, 5)) * 45}px)`, opacity: enter(frame, 5)}}><Headline eyebrow="Your Career. In Any Format." title={<>Your academic record,<br />ready for what comes next.</>} body="VitaMine makes the CV you already have useful everywhere it needs to be." /></div>
      <div style={{position: 'absolute', right: 80, bottom: 40, width: 840, height: 720, borderRadius: '50% 50% 0 0', background: 'linear-gradient(140deg,#b9dfd1,#e6f4ef)', transform: `translateY(${(1 - enter(frame, 20)) * 100}px)`}}><div style={{position: 'absolute', left: 215, top: 145, width: 375, height: 440, borderRadius: '30px 30px 18px 18px', background: colors.white, boxShadow: '0 35px 80px rgba(31,91,74,.18)', transform: 'rotate(6deg)'}}><div style={{padding: 43, color: colors.ink, fontWeight: 800}}>CURRICULUM VITAE<div style={{height: 9, marginTop: 42, borderRadius: 9, background: '#dce7e3'}} /><div style={{height: 9, marginTop: 16, borderRadius: 9, background: '#dce7e3'}} /><div style={{height: 9, marginTop: 16, width: '65%', borderRadius: 9, background: '#dce7e3'}} /></div></div></div>
    </Frame>
    <ProblemScene frame={frame} />
    <ImportScene frame={frame} />
    <SyncScene frame={frame} />
    <PromptScene frame={frame} />
    <ProfileScene frame={frame} />
    <InsightScene frame={frame} />
    <MapScene frame={frame} />
    <ClosingScene frame={frame} />
  </AbsoluteFill>;
};
