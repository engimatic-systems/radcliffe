--------------------------- MODULE BoundedPlayback ---------------------------
EXTENDS Integers, Naturals

CONSTANTS MaxSegments, MaxEpoch, MaxAttempts

SenderStates == {"idle", "announced", "accepted", "uncertain"}
ReceiverStates == {"idle", "accepted", "started", "completed", "flushed"}

VARIABLES epoch,
          nextSegment,
          attemptEpoch,
          attemptSegment,
          attemptNumber,
          senderState,
          receiverState,
          credit,
          heardThrough,
          cancelled,
          connected,
          serverUp,
          receiverUp

vars == <<epoch, nextSegment, attemptEpoch, attemptSegment, attemptNumber,
          senderState, receiverState, credit, heardThrough, cancelled,
          connected, serverUp, receiverUp>>

Init ==
    /\ epoch = 1
    /\ nextSegment = 1
    /\ attemptEpoch = 0
    /\ attemptSegment = 0
    /\ attemptNumber = 0
    /\ senderState = "idle"
    /\ receiverState = "idle"
    /\ credit = 1
    /\ heardThrough = 0
    /\ cancelled = FALSE
    /\ connected = TRUE
    /\ serverUp = TRUE
    /\ receiverUp = TRUE

Admit ==
    /\ serverUp
    /\ connected
    /\ ~cancelled
    /\ senderState = "idle"
    /\ credit = 1
    /\ nextSegment <= MaxSegments
    /\ attemptEpoch' = epoch
    /\ attemptSegment' = nextSegment
    /\ attemptNumber' = attemptNumber + 1
    /\ senderState' = "announced"
    /\ credit' = 0
    /\ UNCHANGED <<epoch, nextSegment, receiverState, heardThrough, cancelled,
                   connected, serverUp, receiverUp>>

DeliverAnnouncement ==
    /\ receiverUp
    /\ connected
    /\ senderState \in {"announced", "uncertain"}
    /\ attemptEpoch = epoch
    /\ receiverState = "idle"
    /\ receiverState' = "accepted"
    /\ UNCHANGED <<epoch, nextSegment, attemptEpoch, attemptSegment,
                   attemptNumber, senderState, credit, heardThrough, cancelled,
                   connected, serverUp, receiverUp>>

AcceptAnnouncement ==
    /\ serverUp
    /\ connected
    /\ ~cancelled
    /\ senderState \in {"announced", "uncertain"}
    /\ receiverState = "accepted"
    /\ attemptEpoch = epoch
    /\ senderState' = "accepted"
    /\ UNCHANGED <<epoch, nextSegment, attemptEpoch, attemptSegment,
                   attemptNumber, receiverState, credit, heardThrough, cancelled,
                   connected, serverUp, receiverUp>>

StartPlayback ==
    /\ receiverUp
    /\ connected
    /\ senderState = "accepted"
    /\ receiverState = "accepted"
    /\ receiverState' = "started"
    /\ UNCHANGED <<epoch, nextSegment, attemptEpoch, attemptSegment,
                   attemptNumber, senderState, credit, heardThrough, cancelled,
                   connected, serverUp, receiverUp>>

CompletePlayback ==
    /\ receiverUp
    /\ receiverState = "started"
    /\ receiverState' = "completed"
    /\ UNCHANGED <<epoch, nextSegment, attemptEpoch, attemptSegment,
                   attemptNumber, senderState, credit, heardThrough, cancelled,
                   connected, serverUp, receiverUp>>

AcceptCompletion ==
    /\ serverUp
    /\ connected
    /\ ~cancelled
    /\ senderState \in {"accepted", "uncertain"}
    /\ receiverState = "completed"
    /\ attemptEpoch = epoch
    /\ attemptSegment = nextSegment
    /\ senderState' = "idle"
    /\ receiverState' = "idle"
    /\ credit' = 1
    /\ heardThrough' = attemptSegment
    /\ nextSegment' = attemptSegment + 1
    /\ attemptNumber' = 0
    /\ UNCHANGED <<epoch, attemptEpoch, attemptSegment,
                   cancelled, connected, serverUp, receiverUp>>

Disconnect ==
    /\ connected
    /\ connected' = FALSE
    /\ senderState' =
        IF senderState \in {"announced", "accepted"} THEN "uncertain" ELSE senderState
    /\ UNCHANGED <<epoch, nextSegment, attemptEpoch, attemptSegment,
                   attemptNumber, receiverState, credit, heardThrough, cancelled,
                   serverUp, receiverUp>>

Reconnect ==
    /\ ~connected
    /\ serverUp
    /\ receiverUp
    /\ connected' = TRUE
    /\ UNCHANGED <<epoch, nextSegment, attemptEpoch, attemptSegment,
                   attemptNumber, senderState, receiverState, credit,
                   heardThrough, cancelled, serverUp, receiverUp>>

ReissueUncertain ==
    /\ serverUp
    /\ receiverUp
    /\ connected
    /\ ~cancelled
    /\ senderState = "uncertain"
    /\ attemptNumber < MaxAttempts
    /\ receiverState \in {"idle", "accepted", "started", "flushed"}
    /\ attemptEpoch' = epoch
    /\ attemptNumber' = attemptNumber + 1
    /\ senderState' = "announced"
    /\ receiverState' = "idle"
    /\ UNCHANGED <<epoch, nextSegment, attemptSegment, credit,
                   heardThrough, cancelled, connected, serverUp, receiverUp>>

Cancel ==
    /\ ~cancelled
    /\ cancelled' = TRUE
    /\ senderState' = "idle"
    /\ receiverState' = "flushed"
    /\ credit' = 1
    /\ UNCHANGED <<epoch, nextSegment, attemptEpoch, attemptSegment,
                   attemptNumber, heardThrough, connected, serverUp, receiverUp>>

BeginGeneration ==
    /\ cancelled
    /\ epoch < MaxEpoch
    /\ epoch' = epoch + 1
    /\ cancelled' = FALSE
    /\ nextSegment' = heardThrough + 1
    /\ attemptEpoch' = 0
    /\ attemptSegment' = 0
    /\ attemptNumber' = 0
    /\ senderState' = "idle"
    /\ receiverState' = "idle"
    /\ credit' = 1
    /\ UNCHANGED <<heardThrough, connected, serverUp, receiverUp>>

CrashServer ==
    /\ serverUp
    /\ serverUp' = FALSE
    /\ connected' = FALSE
    /\ senderState' =
        IF senderState \in {"announced", "accepted"} THEN "uncertain" ELSE senderState
    /\ UNCHANGED <<epoch, nextSegment, attemptEpoch, attemptSegment,
                   attemptNumber, receiverState, credit, heardThrough, cancelled,
                   receiverUp>>

RestartServer ==
    /\ ~serverUp
    /\ epoch < MaxEpoch
    /\ serverUp' = TRUE
    /\ epoch' = epoch + 1
    /\ UNCHANGED <<nextSegment, attemptEpoch, attemptSegment, attemptNumber,
                   senderState, receiverState, credit,
                   heardThrough, cancelled, connected, receiverUp>>

CrashReceiver ==
    /\ receiverUp
    /\ receiverUp' = FALSE
    /\ connected' = FALSE
    /\ receiverState' = "idle"
    /\ senderState' =
        IF senderState \in {"announced", "accepted"} THEN "uncertain" ELSE senderState
    /\ UNCHANGED <<epoch, nextSegment, attemptEpoch, attemptSegment,
                   attemptNumber, credit, heardThrough, cancelled, serverUp>>

RestartReceiver ==
    /\ ~receiverUp
    /\ receiverUp' = TRUE
    /\ UNCHANGED <<epoch, nextSegment, attemptEpoch, attemptSegment,
                   attemptNumber, senderState, receiverState, credit,
                   heardThrough, cancelled, connected, serverUp>>

Next ==
    \/ Admit
    \/ DeliverAnnouncement
    \/ AcceptAnnouncement
    \/ StartPlayback
    \/ CompletePlayback
    \/ AcceptCompletion
    \/ Disconnect
    \/ Reconnect
    \/ ReissueUncertain
    \/ Cancel
    \/ BeginGeneration
    \/ CrashServer
    \/ RestartServer
    \/ CrashReceiver
    \/ RestartReceiver

Spec == Init /\ [][Next]_vars

TypeOK ==
    /\ epoch \in 1..MaxEpoch
    /\ nextSegment \in 1..(MaxSegments + 1)
    /\ attemptEpoch \in 0..MaxEpoch
    /\ attemptSegment \in 0..MaxSegments
    /\ attemptNumber \in 0..MaxAttempts
    /\ senderState \in SenderStates
    /\ receiverState \in ReceiverStates
    /\ credit \in 0..1
    /\ heardThrough \in 0..MaxSegments
    /\ cancelled \in BOOLEAN
    /\ connected \in BOOLEAN
    /\ serverUp \in BOOLEAN
    /\ receiverUp \in BOOLEAN

BoundedAdmission ==
    /\ (senderState = "idle" => credit = 1)
    /\ (senderState # "idle" => credit = 0)

OrderedAdmission ==
    /\ heardThrough < nextSegment
    /\ nextSegment <= heardThrough + 1
    /\ (senderState # "idle" => attemptSegment = nextSegment)

CurrentEpochOnly ==
    senderState \in {"announced", "accepted"} => attemptEpoch = epoch

MetadataBeforeMedia ==
    receiverState \in {"started", "completed"} =>
        senderState \in {"accepted", "uncertain"}

CancellationDominates ==
    cancelled => senderState = "idle"

Safety ==
    TypeOK
    /\ BoundedAdmission
    /\ OrderedAdmission
    /\ CurrentEpochOnly
    /\ MetadataBeforeMedia
    /\ CancellationDominates

=============================================================================
