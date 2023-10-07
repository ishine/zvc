import argparse
import sys
import json
import torchaudio
import os
import glob
import torch

from module.spectrogram import spectrogram
from module.pitch_estimator import PitchEstimator
from module.content_encoder import ContentEncoder
from module.decoder import Decoder
from module.common import match_features

parser = argparse.ArgumentParser()
parser.add_argument('-dep', '--decoder-path', default="decoder.pt")
parser.add_argument('-disp', '--discriminator-path', default="discriminator.pt")
parser.add_argument('-cep', '--content-encoder-path', default="content_encoder.pt")
parser.add_argument('-pep', '--pitch-estimator-path', default="pitch_estimator.pt")
parser.add_argument('-f0', '--f0-rate', default=1.0, type=float)
parser.add_argument('-t', '--target', default='target.wav')
parser.add_argument('-d', '--device', default='cpu')
parser.add_argument('-g', '--gain', default=1.0, type=float)

args = parser.parse_args()

device = torch.device(args.device)

PE = PitchEstimator().to(device)
CE = ContentEncoder().to(device)
Dec = Decoder().to(device)
PE.load_state_dict(torch.load(args.pitch_estimator_path, map_location=device))
CE.load_state_dict(torch.load(args.content_encoder_path, map_location=device))
Dec.load_state_dict(torch.load(args.decoder_path, map_location=device))

if not os.path.exists("./outputs/"):
    os.mkdir("./outputs")

print("encoding target...")
wf, sr = torchaudio.load(args.target)
wf = wf.to(device)
wf = torchaudio.functional.resample(wf, sr, 16000)
tgt = CE(spectrogram(wf)).detach()


paths = glob.glob("./inputs/*.wav")
for i, path in enumerate(paths):
    wf, sr = torchaudio.load(path)
    wf = wf.to(device)
    wf = torchaudio.functional.resample(wf, sr, 16000)
    wf = wf[:1]
    with torch.no_grad():
        print(f"converting {path}")
        spec = spectrogram(wf)
        f0 = PE.estimate(spec) * args.f0_rate
        feat = CE(spec)
        feat = match_features(feat, tgt, k=4)
        wf = Dec(feat, f0)
        
        wf = torchaudio.functional.resample(wf, 16000, sr) * args.gain
    wf = wf.cpu().detach()
    torchaudio.save(filepath=os.path.join("./outputs/", f"{i}.wav"), src=wf, sample_rate=sr)
