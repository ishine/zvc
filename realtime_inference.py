import argparse
import pyaudio
import numpy as np
import torch
import torchaudio
from module.spectrogram import spectrogram
from module.common import match_features
from module.decoder import Decoder
from module.content_encoder import ContentEncoder
from module.pitch_estimator import PitchEstimator
from module.logo import print_logo
import json
import pyaudio
from tqdm import tqdm

print_logo()

parser = argparse.ArgumentParser(description="Convert voice")

audio = pyaudio.PyAudio()
parser.add_argument('-d', '--device', default='cpu', choices=['cpu', 'cuda', 'mps'],
                    help="Compute device setting. Set this option to cuda if you need to use ROCm.")
parser.add_argument('-i', '--input', default=0, type=int)
parser.add_argument('-o', '--output', default=0, type=int)
parser.add_argument('-l', '--loopback', default=-1, type=int)
parser.add_argument('-ig', '--input-gain', default=1.0, type=float)
parser.add_argument('-g', '--gain', default=1.0, type=float)
parser.add_argument('-thr', '--threshold', default=-40.0, type=float)
parser.add_argument('-dep', '--decoder-path', default="decoder.pt")
parser.add_argument('-cep', '--content-encoder-path', default="content_encoder.pt")
parser.add_argument('-pep', '--pitch-estimator-path', default="pitch_estimator.pt")
parser.add_argument('-b', '--buffersize', default=16, type=int)
parser.add_argument('-c', '--chunk', default=3072, type=int)
parser.add_argument('-ic', '--inputchannels', default=1, type=int)
parser.add_argument('-oc', '--outputchannels', default=1, type=int)
parser.add_argument('-lc', '--loopbackchannels', default=1, type=int)
parser.add_argument('-f0', '--f0-rate', default=0, type=float)
parser.add_argument('-t', '--target', default='target.wav')
parser.add_argument('-isr', '--internal-sampling-rate', default=16000, type=int)
parser.add_argument('-k', default=4, type=int)
parser.add_argument('-a', '--alpha', default=0.1, type=float)
parser.add_argument('-compile', default=False, type=bool)
parser.add_argument('-fp16', default=False, type=bool)

args = parser.parse_args()
device_name = args.device

print(f"selected device: {device_name}")
if device_name == 'cuda':
    if not torch.cuda.is_available():
        print("Error: cuda is not available in this environment.")
        exit()

if device_name == 'mps':
    if not torch.backends.mps.is_built():
        print("Error: mps is not available in this environment.")
        exit()

device = torch.device(device_name)
input_buff = []
chunk = args.chunk
buffer_size = args.buffersize


PE = PitchEstimator().to(device)
CE = ContentEncoder().to(device)
Dec = Decoder().to(device)
PE.load_state_dict(torch.load(args.pitch_estimator_path, map_location=device))
CE.load_state_dict(torch.load(args.content_encoder_path, map_location=device))
Dec.load_state_dict(torch.load(args.decoder_path, map_location=device))


print("encoding target...")
wf, sr = torchaudio.load(args.target)
wf = wf.to(device)
wf = torchaudio.functional.resample(wf, sr, 16000)
tgt = CE(spectrogram(wf)).detach()


if args.compile:
    print("Compiling Models...")
    convertor = torch.compile(convertor)
    vocoder = torch.compile(vocoder)
    print("Complete!")

stream_input = audio.open(
        format=pyaudio.paInt16,
        rate=44100,
        channels=args.inputchannels,
        input_device_index=args.input,
        input=True)
stream_output = audio.open(
        format=pyaudio.paInt16,
        rate=44100, 
        channels=args.outputchannels,
        output_device_index=args.output,
        output=True)
stream_loopback = audio.open(
        format=pyaudio.paInt16,
        rate=44100, 
        channels=args.loopbackchannels,
        output_device_index=args.loopback,
        output=True) if args.loopback != -1 else None

print("Converting Voice...")
print("")
bar = tqdm()
while True:
    data = stream_input.read(chunk, exception_on_overflow=False)
    data = np.frombuffer(data, dtype=np.int16)
    input_buff.append(data)
    if len(input_buff) > buffer_size:
        del input_buff[0]
    else:
        continue
    if not data.max() > args.threshold:
        data = data * 0
    data = np.concatenate(input_buff, 0)
    data = data.astype(np.float32) / 32768 # convert -1 to 1
    data = torch.from_numpy(data).to(device)
    data = torch.unsqueeze(data, 0)
    data = data * args.input_gain
    with torch.no_grad():
        with torch.cuda.amp.autocast(enabled=args.fp16):
            # Downsample
            data = torchaudio.functional.resample(data, 44100, 16000)
            # Calculate loudness
            loudness = torchaudio.functional.loudness(data, 16000)
            if loudness.item() > args.threshold:
                # to spectrogram
                spec = spectrogram(data)
                # convert voice
                content = CE(spec)
                pitch = PE.estimate(spec) * args.f0_rate
                content = match_features(content, tgt, k=args.k, alpha=args.alpha)
                data = Dec(content, pitch)

                bar.set_description(desc=f"Loudness: {loudness+80:.4f} dB, F0: {pitch.mean().item():.4f} Hz")

            else:
                data = data * 0
            # gain
            data = torchaudio.functional.gain(data, args.gain)
            # Upsample
            data = torchaudio.functional.resample(data, 16000, args.internal_sampling_rate)
            data = torchaudio.functional.resample(data, args.internal_sampling_rate, 44100)
            data = data[0]

    data = data.cpu().numpy()
    data = (data) * 32768
    data = data
    data = data.astype(np.int16)
    s = (chunk * buffer_size) // 2 - (chunk // 2)
    e = (chunk * buffer_size) - s
    data = data[s:e]
    data = data.tobytes()
    stream_output.write(data)
    if stream_loopback is not None:
        stream_loopback.write(data)
    bar.update(0)