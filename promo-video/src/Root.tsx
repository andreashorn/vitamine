import {Composition} from 'remotion';
import {PromoFilm} from './PromoFilm';

export const RemotionRoot = () => {
  return (
    <Composition
      id="VitaMinePromo"
      component={PromoFilm}
      durationInFrames={2400}
      fps={30}
      width={1920}
      height={1080}
    />
  );
};
