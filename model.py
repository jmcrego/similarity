# -*- coding: utf-8 -*-
import tensorflow as tf
import numpy as np
import math
import sys
import os
import time
from random import randint
from config import Config
from dataset import minibatches

class Score():
    def __init__(self):
        self.TP = 0
        self.TN = 0
        self.FP = 0
        self.FN = 0
        self.A = 0
        self.P = 0
        self.R = 0
        self.F = 0

    def add(self, p, r): ### prediction, reference
        # when r < 0 => positive example, alignment exists (parallel sentence)
        # when p > 0 => alignment exists in matrix (similarity is high)
        if p*r <= 0:
            if p >= 0: 
                self.TP += 1 #alignment predicted
            else: 
                self.TN += 1 #alignment not predicted
        else:
            if p >= 0: 
                self.FP += 1
            else: 
                self.FN += 1
        #print("Pred:{} Ref:{}, TP:{} TN:{} FP:{} FN:{}".format(p, r, self.TP, self.TN, self.FP, self.FN))

    def add_batch_tokens(self, p, r, l):
        for s in range(len(l)): ### sentence s of batch
            for w in range(l[s]): ### all words in sentence s (length is l[s])
                self.add(p[s][w],r[s][w])

    def add_batch(self, p, r):
        for s in range(len(p)): ### all sentences in batch
            self.add(p[s],r[s])

    def update(self):
        self.A, self.P, self.R, self.F = 0., 0., 0., 0.
        if (self.TP + self.FP) > 0: self.P = 1. * self.TP / (self.TP + self.FP) #true positives out of all that were predicted positive
        if (self.TP + self.FN) > 0: self.R = 1. * self.TP / (self.TP + self.FN) #true positives out of all that were actually positive
        if (self.P + self.R) > 0: self.F = 2. * self.P * self.R / (self.P + self.R)
        if (self.TP + self.TN + self.FP + self.FN) > 0: self.A = 1.0 * (self.TP + self.TN) / (self.TP + self.TN + self.FP + self.FN)

class Model():
    def __init__(self, config):
        self.config = config
        self.sess = None

    def embedding_initialize(self,NS,ES,embeddings):
        if embeddings is not None: 
            m = embeddings.matrix
        else:
            m = tf.random_uniform([NS, ES], minval=-0.1, maxval=0.1)
        return m

###################
### build graph ###
###################

    def add_placeholders(self):
        self.input_src  = tf.placeholder(tf.int32, shape=[None,None], name="input_src")  # Shape: batch_size x |Fj|  (all sentences Fj are equally sized (padded if needed))  
        self.input_tgt  = tf.placeholder(tf.int32, shape=[None,None], name="input_tgt")  # Shape: batch_size x |Ei|  (all sentences Ej are equally sized (padded if needed))  
        self.sign_src   = tf.placeholder(tf.float32, shape=[None,None], name="sign_src") # Shape: batch_size x |Ei| 
        self.sign_tgt   = tf.placeholder(tf.float32, shape=[None,None], name="sign_src") # Shape: batch_size x |Fi| 
        self.sign       = tf.placeholder(tf.float32, shape=[None], name="sign")          # Shape: batch_size (sign of each sentence: {1,-1}) 
        self.len_src    = tf.placeholder(tf.int32, shape=[None], name="len_src")
        self.len_tgt    = tf.placeholder(tf.int32, shape=[None], name="len_tgt")
        self.lr         = tf.placeholder(tf.float32, shape=[], name="lr")

    def add_model(self):
        BS = tf.shape(self.input_src)[0] #batch size
        KEEP = 1.0-self.config.dropout   # keep probability for embeddings dropout Ex: 0.7
#        print("KEEP={}".format(KEEP))

        ###
        ### src-side
        ###
        NW = self.config.src_voc_size #src vocab
        ES = self.config.src_emb_size #src embedding size
        L1 = self.config.src_lstm_size #src lstm size
#        print("SRC NW={} ES={}".format(NW,ES))
        with tf.device('/cpu:0'), tf.name_scope("embedding_src"):
            self.LT_src = tf.get_variable(initializer = self.embedding_initialize(NW, ES, self.config.emb_src), dtype=tf.float32, name="embeddings_src")
            self.embed_src = tf.nn.embedding_lookup(self.LT_src, self.input_src, name="embed_src")
            self.embed_src = tf.nn.dropout(self.embed_src, keep_prob=KEEP)

        with tf.variable_scope("lstm_src"):
#            print("SRC L1={}".format(L1))
            cell_fw = tf.contrib.rnn.LSTMCell(L1, state_is_tuple=True)
            cell_bw = tf.contrib.rnn.LSTMCell(L1, state_is_tuple=True)
            ini_fw = cell_fw.zero_state(BS,dtype=tf.float32)
            ini_bw = cell_bw.zero_state(BS,dtype=tf.float32)
            (output_fw, output_bw), (last_fw, last_bw) = tf.nn.bidirectional_dynamic_rnn(cell_fw, cell_bw, self.embed_src, initial_state_fw = ini_fw, initial_state_bw = ini_bw, sequence_length=self.len_src, dtype=tf.float32)
            ### divergent
            self.last_src = tf.concat([last_fw[1], last_bw[1]], axis=1)
            self.last_src = tf.nn.dropout(self.last_src, keep_prob=KEEP)
            ### alignment
            self.out_src = tf.concat([output_fw, output_bw], axis=2)
            self.out_src = tf.nn.dropout(self.out_src, keep_prob=KEEP)

        ###
        ### tgt-side
        ###
        NW = self.config.tgt_voc_size #tgt vocab
        ES = self.config.tgt_emb_size #tgt embedding size
        L1 = self.config.tgt_lstm_size #tgt lstm size
#        print("TGT NW={} ES={}".format(NW,ES))
        with tf.device('/cpu:0'), tf.name_scope("embedding_tgt"):
            self.LT_tgt = tf.get_variable(initializer = self.embedding_initialize(NW, ES, self.config.emb_tgt), dtype=tf.float32, name="embeddings_tgt")
            self.embed_tgt = tf.nn.embedding_lookup(self.LT_tgt, self.input_tgt, name="input_matrix_tgt")
            self.embed_tgt = tf.nn.dropout(self.embed_tgt, keep_prob=KEEP)
        with tf.variable_scope("lstm_tgt"):
#            print("TGT L1={}".format(L1))
            cell_fw = tf.contrib.rnn.LSTMCell(L1, state_is_tuple=True)
            cell_bw = tf.contrib.rnn.LSTMCell(L1, state_is_tuple=True)
            ini_fw = cell_fw.zero_state(BS,dtype=tf.float32)
            ini_bw = cell_bw.zero_state(BS,dtype=tf.float32)
            (output_fw, output_bw), (last_fw, last_bw) = tf.nn.bidirectional_dynamic_rnn(cell_fw, cell_bw, self.embed_tgt, initial_state_fw = ini_fw, initial_state_bw = ini_bw, sequence_length=self.len_tgt, dtype=tf.float32)
            ### divergent
            self.last_tgt = tf.concat([last_fw[1], last_bw[1]], axis=1)                
            self.last_tgt = tf.nn.dropout(self.last_tgt, keep_prob=KEEP)
            ### alignment
            self.out_tgt = tf.concat([output_fw, output_bw], axis=2)
            self.out_tgt = tf.nn.dropout(self.out_tgt, keep_prob=KEEP)

        ###
        ### sentence (always computed)
        ###
        # next is a tensor containing similarity distances (one for each sentence pair)
        #sum(a*b for a,b in zip(x,y)) / (square_rooted(x)*square_rooted(y))
        self.cos_similarity = tf.reduce_sum(tf.nn.l2_normalize(self.last_src, dim=1) * tf.nn.l2_normalize(self.last_tgt, dim=1), axis=1) ### +1:similar -1:divergent
        ###
        ### alignment
        ###
        if self.config.mode == "alignment":
            R = self.config.r
#            print("R={}".format(R))
            with tf.name_scope("align"):
                self.align = tf.map_fn(lambda (x,y): tf.matmul(x,tf.transpose(y)), (self.out_src, self.out_tgt), dtype = tf.float32, name="align")
            with tf.name_scope("aggregation"):
                if self.config.aggr == "lse":
                    self.aggregation_src = tf.divide(tf.log(tf.map_fn(lambda (x,l) : tf.reduce_sum(x[:l,:],0), (tf.exp(tf.transpose(self.align,[0,2,1]) * R), self.len_tgt) , dtype=tf.float32)), R, name="aggregation_src")
                    self.aggregation_tgt = tf.divide(tf.log(tf.map_fn(lambda (x,l) : tf.reduce_sum(x[:l,:],0), (tf.exp(self.align * R), self.len_src) , dtype=tf.float32)), R, name="aggregation_tgt")
                elif self.config.aggr == "sum":
                    self.aggregation_src = tf.map_fn(lambda (x,l) : tf.reduce_sum(x[:l,:],0), (tf.transpose(self.align,[0,2,1]), self.len_tgt), dtype=tf.float32, name="aggregation_src")
                    self.aggregation_tgt = tf.map_fn(lambda (x,l) : tf.reduce_sum(x[:l,:],0), (self.align, self.len_src), dtype=tf.float32, name="aggregation_tgt")
                elif self.config.aggr == "max":
                    self.aggregation_src = tf.map_fn(lambda (x,l) : tf.reduce_max(x[:l,:],axis=0), (tf.transpose(self.align,[0,2,1]) , self.len_tgt), dtype=tf.float32, name="aggregation_src")
                    self.aggregation_tgt = tf.map_fn(lambda (x,l) : tf.reduce_max(x[:l,:],axis=0), (self.align , self.len_src), dtype=tf.float32, name="aggregation_tgt")
                else:
                    sys.stderr.write("error: bad aggregation option '{}'\n".format(self.config.aggr))
                    sys.exit()
                self.output_src = tf.log(1 + tf.exp(self.aggregation_src * self.sign_src))
                self.output_tgt = tf.log(1 + tf.exp(self.aggregation_tgt * self.sign_tgt))


    def add_loss(self):
        with tf.name_scope("loss"):
            if self.config.mode == "sentence": 
                ###cos_similarity: +1:similar, -1:opposite(divergence)
                ###sign: +1:divergence, -1:similar
                self.loss = tf.reduce_sum(tf.log(1 + tf.exp(self.cos_similarity * self.sign)))
            else:
                self.loss_src = tf.reduce_mean(tf.map_fn(lambda (x,l): tf.reduce_sum(x[:l]), (self.output_src, self.len_src), dtype=tf.float32))
                self.loss_tgt = tf.reduce_mean(tf.map_fn(lambda (x,l): tf.reduce_sum(x[:l]), (self.output_tgt, self.len_tgt), dtype=tf.float32))
                self.loss = self.loss_tgt + self.loss_src

    def add_train(self):
        if   self.config.lr_method == 'adam':     optimizer = tf.train.AdamOptimizer(self.lr)
        elif self.config.lr_method == 'adagrad':  optimizer = tf.train.AdagradOptimizer(self.lr)
        elif self.config.lr_method == 'sgd':      optimizer = tf.train.GradientDescentOptimizer(self.lr)
        elif self.config.lr_method == 'rmsprop':  optimizer = tf.train.RMSPropOptimizer(self.lr)
        elif self.config.lr_method == 'adadelta': optimizer = tf.train.AdadeltaOptimizer(self.lr)
        else:
            sys.stderr.write("error: bad lr_method option '{}'\n".format(self.config.lr_method))
            sys.exit()

        tvars = tf.trainable_variables()
        grads, _ = tf.clip_by_global_norm(tf.gradients(self.loss, tvars),1.0)
        self.train_op = optimizer.apply_gradients(zip(grads, tvars))

    def build_graph(self):
        self.add_placeholders()
        self.add_model()  
        if self.config.tst is None: 
            self.add_loss()
            self.add_train()

###################
### feed_dict #####
###################

    def get_feed_dict(self, input_src, input_tgt, sign_src, sign_tgt, sign, len_src, len_tgt, lr):
        feed = { 
            self.input_src: input_src,
            self.input_tgt: input_tgt,
            self.sign_src: sign_src,
            self.sign_tgt: sign_tgt,
            self.sign: sign,
            self.len_src: len_src,
            self.len_tgt: len_tgt,
            self.lr: lr
        }
        return feed

###################
### learning ######
###################

    def run_eval(self, tst):
        nbatches = (len(tst) + self.config.batch_size - 1) // self.config.batch_size
        # iterate over dataset
        LOSS = 0
        score = Score()
        for iter, (src_batch, tgt_batch, raw_src_batch, raw_tgt_batch, sign_src_batch, sign_tgt_batch, sign_batch, len_src_batch, len_tgt_batch) in enumerate(minibatches(tst, self.config.batch_size)):
            fd = self.get_feed_dict(src_batch, tgt_batch, sign_src_batch, sign_tgt_batch, sign_batch, len_src_batch, len_tgt_batch, 0.0)
            if self.config.mode == "sentence":
                loss, sim = self.sess.run([self.loss, self.cos_similarity], feed_dict=fd)
                score.add_batch(sim, sign_batch)
            else:
                loss, aggr_src, aggr_tgt = self.sess.run([self.loss, self.aggregation_src, self.aggregation_tgt], feed_dict=fd)
                score.add_batch_tokens(aggr_src, sign_src_batch, len_src_batch)
                score.add_batch_tokens(aggr_tgt, sign_tgt_batch, len_tgt_batch)
            LOSS += loss # append single value which is a mean of losses of the n examples in the batch
        score.update()
        return LOSS/nbatches, score

    def run_epoch(self, train, dev, lr):
        nbatches = (len(train) + self.config.batch_size - 1) // self.config.batch_size
        curr_epoch = self.config.last_epoch + 1
        TLOSS = 0 # training loss
        ILOSS = 0 # intermediate loss (average over [config.report_every] iterations)
        tscore = Score()
        iscore = Score()
        ini_time = time.time()
        for iter, (src_batch, tgt_batch, raw_src_batch, raw_tgt_batch, sign_src_batch, sign_tgt_batch, sign_batch, len_src_batch, len_tgt_batch) in enumerate(minibatches(train, self.config.batch_size)):
            fd = self.get_feed_dict(src_batch, tgt_batch, sign_src_batch, sign_tgt_batch, sign_batch, len_src_batch, len_tgt_batch, lr)
            if self.config.mode == "sentence":
                _, loss, sim = self.sess.run([self.train_op, self.loss, self.cos_similarity], feed_dict=fd)
                tscore.add_batch(sim,sign_batch)
                iscore.add_batch(sim,sign_batch)
            else:
                _, loss, aggr_src, aggr_tgt = self.sess.run([self.train_op, self.loss, self.aggregation_src, self.aggregation_tgt], feed_dict=fd)
                tscore.add_batch_tokens(aggr_src, sign_src_batch, len_src_batch)
                tscore.add_batch_tokens(aggr_tgt, sign_tgt_batch, len_tgt_batch)
                iscore.add_batch_tokens(aggr_src, sign_src_batch, len_src_batch)
                iscore.add_batch_tokens(aggr_tgt, sign_tgt_batch, len_tgt_batch)
            TLOSS += loss
            ILOSS += loss

            if (iter+1)%self.config.report_every == 0: 
                curr_time = time.strftime("[%Y-%m-%d_%X]", time.localtime())
                iscore.update()
                ILOSS = ILOSS/self.config.report_every
                sys.stdout.write('{} Epoch {} Iteration {}/{} batch_size={} lr:{:.4f} loss:{:.4f} (A{:.4f},P{:.4f},R{:.4f},F{:.4f})\n'.format(curr_time,curr_epoch,iter+1,nbatches,self.config.batch_size,lr,ILOSS,iscore.A,iscore.P,iscore.R,iscore.F))
                ILOSS = 0
                iscore = Score()

        TLOSS = TLOSS/nbatches
        tscore.update()
        curr_time = time.strftime("[%Y-%m-%d_%X]", time.localtime())
        sys.stdout.write('{} Epoch {}'.format(curr_time,curr_epoch))
        sys.stdout.write('{} TRAINING loss={:.4f} (A{:.4f},P{:.4f},R{:.4f},F{:.4f}) lr={:.4f}'.format(curr_time,TLOSS,tscore.A,tscore.P,tscore.R,tscore.F,lr))

        # evaluate over devset
        if self.config.dev is not None:
            VLOSS, vscore = self.run_eval(dev)
            sys.stdout.write('{} VALIDATION loss={:.4f} (A{:.4f},P{:.4f},R{:.4f},F{:.4f})\n'.format(curr_time,VLOSS,vscore.A,vscore.P,vscore.R,vscore.F))
        else:
            sys.stdout.write('\n')
            VLOSS = 0.0

        unk_src = float(100) * train.nunk_src / train.nsrc
        unk_tgt = float(100) * train.nunk_tgt / train.ntgt
        div_src = float(100) * train.ndiv_src / train.nsrc
        div_tgt = float(100) * train.ndiv_tgt / train.ntgt
        sys.stdout.write('{} Training set: words={}/{} %div={:.2f}/{:.2f} %unk={:.2f}/{:.2f}\n'.format(curr_time,train.nsrc,train.ntgt,div_src,div_tgt,unk_src,unk_tgt))
        if self.config.dev is not None:
            unk_src = float(100) * dev.nunk_src / dev.nsrc
            unk_tgt = float(100) * dev.nunk_tgt / dev.ntgt
            div_src = float(100) * dev.ndiv_src / dev.nsrc
            div_tgt = float(100) * dev.ndiv_tgt / dev.ntgt
            sys.stdout.write('{} Validation set words={}/{} %div={:.2f}/{:.2f} %unk={:.2f}/{:.2f}\n'.format(curr_time,dev.nsrc,dev.ntgt,div_src,div_tgt,unk_src,unk_tgt,VLOSS,vscore.A,vscore.P,vscore.R,vscore.F))

        #keep record of current epoch
        self.config.tloss = TLOSS
        self.config.tA = tscore.A
        self.config.tP = tscore.P
        self.config.tR = tscore.R
        self.config.tF = tscore.F
        self.config.time = time.strftime("[%Y-%m-%d_%X]", time.localtime())
        self.config.seconds = "{:.2f}".format(time.time() - ini_time)
        self.config.last_epoch += 1
        self.save_session(self.config.last_epoch)
        if self.config.dev is not None:
            self.config.vloss = VLOSS
            self.config.vA = vscore.A
            self.config.vP = vscore.P
            self.config.vR = vscore.R
            self.config.vF = vscore.F
        self.config.write_config()
        return VLOSS, curr_epoch


    def learn(self, train, dev, n_epochs):
        lr = self.config.lr
        curr_time = time.strftime("[%Y-%m-%d_%X]", time.localtime())
        sys.stdout.write("{} Training with {} examples (sentence pairs): {} batches.\n".format(curr_time,len(train),(len(train)+self.config.batch_size-1)//self.config.batch_size))
        best_score = 0
        best_epoch = 0
        for iter in range(n_epochs):
            score, epoch = self.run_epoch(train, dev, lr)  ### decay when score does not improve over the best
            curr_time = time.strftime("[%Y-%m-%d_%X]", time.localtime())
            if iter == 0 or score <= best_score: 
                best_score = score
                best_epoch = epoch
            else:
                lr *= self.config.lr_decay # decay learning rate

###################
### inference #####
###################

    def run_tst(self, tst):
        nbatches = (len(tst) + self.config.batch_size - 1) // self.config.batch_size
        # iterate over dataset
        score = Score()
        for iter, (src_batch, tgt_batch, raw_src_batch, raw_tgt_batch, sign_src_batch, sign_tgt_batch, sign_batch, len_src_batch, len_tgt_batch) in enumerate(minibatches(tst, self.config.batch_size)):
            fd = self.get_feed_dict(src_batch, tgt_batch, sign_src_batch, sign_tgt_batch, sign_batch, len_src_batch, len_tgt_batch, 0.0)
            if self.config.mode == "sentence":
                sim = self.sess.run(self.cos_similarity, feed_dict=fd)
                score.add_batch(sim, sign_batch)
            else:
                aggr_src, aggr_tgt = self.sess.run([self.aggregation_src, self.aggregation_tgt], feed_dict=fd)
                score.add_batch_tokens(aggr_src, sign_src_batch, len_src_batch)
                score.add_batch_tokens(aggr_tgt, sign_tgt_batch, len_tgt_batch)
        score.update()
        return score

    def inference(self, tst):

        if tst.annotated and not self.config.show_svg and not self.config.show_matrix and not self.config.show_last and not self.config.show_aggr and not self.config.show_align:
            score = self.run_tst(tst)
            unk_s = float(100) * tst.nunk_src / tst.nsrc
            unk_t = float(100) * tst.nunk_tgt / tst.ntgt
            div_s = float(100) * tst.ndiv_src / tst.nsrc
            div_t = float(100) * tst.ndiv_tgt / tst.ntgt
            sys.stdout.write('TEST words={}/{} %div={:.2f}/{:.2f} %unk={:.2f}/{:.2f} (A{:.4f},P{:.4f},R{:.4f},F{:.4f}) (TP:{},TN:{},FP:{},FN:{})\n'.format(tst.nsrc,tst.ntgt,div_s,div_t,unk_s,unk_t,score.A,score.P,score.R,score.F,score.TP,score.TN,score.FP,score.FN))
            return

        if self.config.show_svg: print "<html>\n<body>"
        nbatches = (len(tst) + self.config.batch_size - 1) // self.config.batch_size
        n_sents = 0
        for iter, (src_batch, tgt_batch, raw_src_batch, raw_tgt_batch, sign_src_batch, sign_tgt_batch, sign_batch, len_src_batch, len_tgt_batch) in enumerate(minibatches(tst, self.config.batch_size)):
            fd = self.get_feed_dict(src_batch, tgt_batch, sign_src_batch, sign_tgt_batch, sign_batch, len_src_batch, len_tgt_batch, 0.0) 

            if self.config.mode == "sentence":
                sim_batch, last_src_batch, last_tgt_batch = self.sess.run([self.cos_similarity, self.last_src, self.last_tgt], feed_dict=fd)
                for i_sent in range(len(sim_batch)):
                    raw_src =  " ".join(str(s) for s in raw_src_batch[i_sent])
                    raw_tgt =  " ".join(str(s) for s in raw_tgt_batch[i_sent])
                    if self.config.show_last:
                        last_src = " ".join(str(s) for s in last_src_batch[i_sent])
                        last_tgt = " ".join(str(s) for s in last_tgt_batch[i_sent])
                        print ("{:.4f}\t{}\t{}\t{}\t{}".format(sim_batch[i_sent], raw_src, raw_tgt, last_src, last_tgt))
                    else:
                        print ("{:.4f}\t{}\t{}".format(sim_batch[i_sent], raw_src, raw_tgt))
            else:   
                align_batch, aggr_src_batch, aggr_tgt_batch, out_src_batch, out_tgt_batch, last_src_batch, last_tgt_batch, sim_batch = self.sess.run([self.align, self.aggregation_src, self.aggregation_tgt, self.out_src, self.out_tgt, self.last_src, self.last_tgt, self.cos_similarity], feed_dict=fd)
                for i_sent in range(len(align_batch)):
                    n_sents += 1
                    if self.config.show_svg: 
                        self.print_svg(n_sents,raw_src_batch[i_sent],raw_tgt_batch[i_sent],align_batch[i_sent],aggr_src_batch[i_sent],aggr_tgt_batch[i_sent],sim_batch[i_sent])
                    elif self.config.show_matrix: 
                        self.print_matrix(n_sents,raw_src_batch[i_sent],raw_tgt_batch[i_sent],align_batch[i_sent],aggr_src_batch[i_sent],aggr_tgt_batch[i_sent],sim_batch[i_sent])
                    else:
                        toks = []
                        raw_src =  " ".join(str(s) for s in raw_src_batch[i_sent])
                        raw_tgt =  " ".join(str(s) for s in raw_tgt_batch[i_sent])
                        toks.append(raw_src)
                        toks.append(raw_tgt)
                        if self.config.show_last: 
                            last_src = " ".join("{:.4f}".format(s) for s in last_src_batch[i_sent])
                            last_tgt = " ".join("{:.4f}".format(t) for t in last_tgt_batch[i_sent])
                            toks.append(last_src)
                            toks.append(last_tgt)
                        if self.config.show_aggr: 
                            aggr_src = " ".join(str("{:.4f}".format(aggr_src_batch[i_sent][s])) for s in range(len_src_batch[i_sent]))
                            aggr_tgt = " ".join(str("{:.4f}".format(aggr_tgt_batch[i_sent][s])) for s in range(len_tgt_batch[i_sent]))
                            toks.append(aggr_src)
                            toks.append(aggr_tgt)
                        if self.config.show_align: 
                            matrix_row = []
                            for s in range(len_src_batch[i_sent]):
                                row = " ".join("{:.4f}".format(align_batch[i_sent,s,t]) for t in range(len_tgt_batch[i_sent]))
                                matrix_row.append(row)
                            matrix = "\t".join(row for row in matrix_row)
                            toks.append(matrix)
                        print ("{:.4f}\t{}".format(sim_batch[i_sent], "\t".join(toks)))

        if self.config.show_svg: print "</body>\n</html>"


    def print_matrix(self, n_sents, src, tgt, align, aggr_src, aggr_tgt, sim):
        print('<:::{}:::> cosine sim = {:.4f}'.format(n_sents, sim))
        source = list(src)
        target = list(tgt)
        for s in range(len(source)):
            if aggr_src[s]<0: source[s] = '*'+source[s] 
        for t in range(len(target)):
            if aggr_tgt[t]<0: target[t] = '*'+target[t] 

        max_length_tgt_tokens = max(5,max([len(x) for x in target]))
        A = str(max_length_tgt_tokens+1)
        print(''.join(("{:"+A+"}").format(t) for t in target))
        for s in range(len(source)):
            for t in range(len(target)):
                myscore = "{:+.2f}".format(align[s][t])
                while len(myscore) < max_length_tgt_tokens+1: myscore += ' '
                sys.stdout.write(myscore)
            print(source[s])

    def print_svg(self, n_sents, src, tgt, align, aggr_src, aggr_tgt, sim):
        start_x = 25
        start_y = 100
        len_square = 15
        len_x = len(tgt)
        len_y = len(src)
        separation = 2
        print "<br>\n<svg width=\""+str(len_x*len_square + start_x + 100)+"\" height=\""+str(len_y*len_square + start_y)+"\">"
        for x in range(len(tgt)): ### tgt
            if aggr_tgt[x]<0: col="red"
            else: col="black"
            col="black"
            print "<text x=\""+str(x*len_square + start_x + separation)+"\" y=\""+str(start_y-10)+"\" fill=\""+col+"\" font-family=\"Courier\" font-size=\"10\" transform=\"rotate(-45 "+str(x*len_square + start_x + 10)+","+str(start_y-10)+") \">"+tgt[x]+"&nbsp;{:+.1f}".format(aggr_tgt[x])+"</text>"
        for y in range(len(src)): ### src
            for x in range(len(tgt)): ### tgt
                color = align[y][x]
                if color < 0: color = 1
                elif color > 10: color = 0
                else: color = (-color+10)/10
                color = int(color*256)
                print "<rect x=\""+str(x*len_square + start_x)+"\" y=\""+str(y*len_square + start_y)+"\" width=\""+str(len_square)+"\" height=\""+str(len_square)+"\" style=\"fill:rgb("+str(color)+","+str(color)+","+str(color)+"); stroke-width:1;stroke:rgb(200,200,200)\" />"
                txtcolor = "black"
                if align[y][x] < 0: txtcolor="red"
                print "<text x=\""+str(x*len_square + start_x + len_square*1/6)+"\" y=\""+str(y*len_square + start_y + len_square*3/4)+"\" fill=\"{}\" font-family=\"Courier\" font-size=\"5\">".format(txtcolor)+"{:+.1f}".format(align[y][x])+"</text>"


            if aggr_src[y]<0: col="red" ### last column with source words
            else: col="black"
            col="black" ### remove this line if you want divergent colors in red
            print "<text x=\""+str(len_x*len_square + start_x + separation)+"\" y=\""+str(y*len_square + start_y + len_square*3/4)+"\" fill=\""+col+"\" font-family=\"Courier\" font-size=\"10\">"+src[y]+"&nbsp;{:+.1f}".format(aggr_src[y])+"</text>"
        print("<br>\n<svg width=\"200\" height=\"20\">")
        print("<text x=\"{}\" y=\"10\" fill=\"black\" font-family=\"Courier\" font-size=\"8\"\">{:+.4f}</text>".format(start_x,sim))

###################
### session #######
###################

    def initialize_session(self):
        self.sess = tf.Session()
        self.saver = tf.train.Saver(max_to_keep=20)

        if self.config.epoch is not None: ### restore a file for testing
            fmodel = self.config.mdir + '/epoch' + self.config.epoch
            sys.stderr.write("Restoring model: {}\n".format(fmodel))
            self.saver.restore(self.sess, fmodel)
            return

        if self.config.mdir: ### initialize for training or restore previous
            if not os.path.exists(self.config.mdir + '/checkpoint'): 
                sys.stderr.write("Initializing model\n")
                self.sess.run(tf.global_variables_initializer())
            else:
                sys.stderr.write("Restoring previous model: {}\n".format(self.config.mdir))
                self.saver.restore(self.sess, tf.train.latest_checkpoint(self.config.mdir))

    def save_session(self,e):
        if not os.path.exists(self.config.mdir): os.makedirs(self.config.mdir)
        file = "{}/epoch{}".format(self.config.mdir,e)
        self.saver.save(self.sess, file) #, max_to_keep=4, write_meta_graph=False) # global_step=step, keep_checkpoint_every_n_hours=2

    def close_session(self):
        self.sess.close()


