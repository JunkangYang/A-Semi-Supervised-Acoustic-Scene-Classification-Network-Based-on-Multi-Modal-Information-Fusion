#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Jisheng Bai, Haohe Liu, Han Yin, Mou Wang
@email: baijs@xupt.edu.cn
# Xi'an University of Posts & Telecommunications, China
# Joint Laboratory of Environmental Sound Sensing, School of Marine Science and Technology, Northwestern Polytechnical University, China
# Xi'an Lianfeng Acoustic Technologies Co., Ltd., China
# University of Surrey, UK
# This software is distributed under the terms of the License MIT
"""
import numpy as np
import scipy
import librosa
import os
from tqdm import tqdm
import pandas as pd
import glob
import config
import argparse

def gen_mel_features(
    data,
    sr,
    n_fft,
    hop_length,
    win_length,
    n_mels,
    fmin,
    fmax,
    window="hann",
    logarithmic=True,
):
    """
    :param data: input waveform
    :param sr: sampling rate
    :param n_fft: FFT samples
    :param hop_length: frame move samples
    :param win_length: window length
    :param n_mels: number of mel bands
    :param fmin: minimum frequency
    :param fmax: maximum frequency
    :param window: window type

    """
    eps = np.spacing(1)
    if window == "hann":
        window = scipy.signal.hann(win_length, sym=False)
    else:
        window = scipy.signal.hamming(win_length, sym=False)
    spectrogram = np.abs(
        librosa.stft(
            data + eps,
            n_fft=n_fft,
            win_length=win_length,
            hop_length=hop_length,
            center=True,
            window=window,
        )
    )
    mel_basis = librosa.filters.mel(
        sr=sr, n_fft=n_fft, n_mels=n_mels, fmin=fmin, fmax=fmax, htk=False
    )

    mel_spectrogram = np.dot(mel_basis, spectrogram)
    log_mel_spectrogram = librosa.power_to_db(mel_spectrogram)

    return log_mel_spectrogram.T


def save_features(config, fold):
    """
    :param config: configuration module
    :param fold: "dev"/"eval" for development/evaluation set
    """
    print("========== Generate Feature for {} ==========".format(fold))
    if fold == "dev" and config.dev_audio_root_path:
        audio_root_path = config.dev_audio_root_path
        meta_csv = pd.read_csv(config.dev_meta_csv_path)
        feature_root_path = config.dev_fea_root_path
        
    elif fold == "eval" and config.eval_audio_root_path:
        audio_root_path = config.eval_audio_root_path
        meta_csv = pd.read_csv(config.eval_meta_csv_path)
        feature_root_path = config.eval_fea_root_path

    elif fold == "pre" and config.pre_train_dev_audio_root_path:
        audio_root_path = config.pre_train_dev_audio_root_path
        meta_csv = pd.read_csv(config.pre_train_dev_meta_csv_path)
        feature_root_path = config.pre_train_fea_root_path
    
    else:
        print("========== Missing {} data ==========".format(fold))
        return
        
    os.makedirs(feature_root_path, exist_ok=True)
    # extract acoustic features
    print("=== Extraction Begin ===")
    with tqdm(total=len(meta_csv)) as pbar:
        for index, row in meta_csv.iterrows():
            filename = row["filename"]
            feature_save_path = os.path.join(feature_root_path, filename + ".npy")
            if os.path.exists(feature_save_path):
                pbar.update(1)
                continue
            
            filepath_str = os.path.join(
                audio_root_path, "*" + filename + "*.wav"
            )
            # print(glob.glob(filepath_str))
            audio_path = glob.glob(filepath_str)[0]
            audio, _ = librosa.load(audio_path, sr=config.sample_rate)

            feature = gen_mel_features(
                audio,
                config.sample_rate,
                config.n_fft,
                config.hop_length,
                config.win_length,
                config.n_mels,
                config.fmin,
                config.fmax,
            )
            
            np.save(feature_save_path, feature, allow_pickle=True)
            pbar.update(1)

        print("========== End ==========")


if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description="feature-extraction")
    parser.add_argument("--dataset", type=str, default="dev", help="dev or eval or pre")
    folder = args = parser.parse_args().dataset
    save_features(config, fold=folder)

