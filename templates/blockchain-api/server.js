const express = require('express');
const cors = require('cors');
const bodyParser = require('body-parser');
const crypto = require('crypto');

const app = express();
app.use(cors());
app.use(bodyParser.json());

app.post('/api/blockchain/vote', async (req, res) => {
    try {
        const { electionId, studentMatric, candidateId } = req.body;

        console.log(`[BLOCKCHAIN REQUEST] Received vote from Matric: ${studentMatric}`);
        console.log(`Election ID: ${electionId} | Candidate(s): ${candidateId}`);

        const dummyTxId = "0x" + crypto.randomBytes(16).toString('hex');

        res.status(200).json({
            success: true,
            message: "Vote successfully securely committed to the ledger.",
            txId: dummyTxId
        });

    } catch (error) {
        console.error("Ledger Error:", error);
        res.status(500).json({ success: false, message: error.message });
    }
});

const PORT = 3000;
app.listen(PORT, () => {
    console.log(`🚀 Blockchain Bridge API is running on http://localhost:${PORT}`);
});